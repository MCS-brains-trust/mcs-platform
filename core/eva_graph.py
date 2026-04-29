"""
Eva Client Knowledge Graph Service
====================================
Provides graph traversal over the existing EntityRelationship model using
PostgreSQL recursive CTEs. This allows Eva to answer complex cross-entity
compliance questions such as:

  - "Does this company have any related-party loans that could trigger Div7A?"
  - "Who are the common directors across all entities in this family group?"
  - "What is the full ownership chain from this trust to its ultimate beneficiaries?"

The GraphQueryService is exposed as a callable tool in the Eva Agentic Orchestrator
(eva_agent.py) under the name 'query_client_graph'.

Architecture:
  - All traversal uses PostgreSQL recursive CTEs for efficiency.
  - Results are returned as plain Python dicts (JSON-serialisable) so they
    can be injected directly into the agent's context window.
  - Maximum traversal depth is capped at 5 to prevent infinite loops in
    circular relationship graphs.

Usage:
    from core.eva_graph import GraphQueryService

    svc = GraphQueryService(entity_id="<uuid>")
    related = svc.traverse_related_entities(depth=2)
    chain = svc.find_div7a_exposure_chain()
    roles = svc.get_officer_cross_entity_roles()
"""

import logging
from typing import Optional
from django.db import connection

logger = logging.getLogger(__name__)

MAX_DEPTH = 5  # Maximum graph traversal depth


class GraphQueryService:
    """
    Graph traversal service for a single root entity.

    All methods return JSON-serialisable Python dicts/lists.
    """

    def __init__(self, entity_id: str):
        self.entity_id = str(entity_id)

    # -----------------------------------------------------------------------
    # Core Traversal
    # -----------------------------------------------------------------------
    def traverse_related_entities(self, depth: int = 2) -> dict:
        """
        Traverse all EntityRelationship edges from this entity up to `depth` hops.

        Returns a dict with:
          - root_entity: {id, name, type}
          - nodes: list of {id, name, type, distance}
          - edges: list of {from_id, to_id, relationship_type, label}
          - summary: plain-English summary of the relationship network
        """
        depth = min(depth, MAX_DEPTH)

        sql = """
            WITH RECURSIVE entity_graph AS (
                -- Base case: the root entity
                SELECT
                    e.id::text AS entity_id,
                    e.entity_name,
                    e.entity_type,
                    NULL::text AS from_entity_id,
                    NULL::text AS relationship_type,
                    0 AS depth
                FROM core_entity e
                WHERE e.id = %s::uuid

                UNION ALL

                -- Recursive case: follow relationships
                SELECT
                    e2.id::text,
                    e2.entity_name,
                    e2.entity_type,
                    eg.entity_id,
                    er.relationship_type,
                    eg.depth + 1
                FROM entity_graph eg
                JOIN core_entityrelationship er
                    ON er.from_entity_id::text = eg.entity_id
                JOIN core_entity e2
                    ON e2.id = er.to_entity_id
                WHERE eg.depth < %s
                  AND e2.is_archived = false
            )
            SELECT DISTINCT
                entity_id,
                entity_name,
                entity_type,
                from_entity_id,
                relationship_type,
                depth
            FROM entity_graph
            ORDER BY depth, entity_name;
        """

        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, [self.entity_id, depth])
                rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"Graph traversal failed for entity {self.entity_id}: {e}")
            return self._error_result("Graph traversal failed")

        nodes = []
        edges = []
        root_entity = None

        for row in rows:
            entity_id, entity_name, entity_type, from_id, rel_type, dist = row
            node = {
                "id": entity_id,
                "name": entity_name,
                "type": entity_type,
                "distance": dist,
            }
            nodes.append(node)

            if dist == 0:
                root_entity = node
            elif from_id and rel_type:
                edges.append({
                    "from_id": from_id,
                    "to_id": entity_id,
                    "relationship_type": rel_type,
                    "label": rel_type.replace("_", " ").title(),
                })

        if not root_entity:
            return self._error_result("Root entity not found")

        # Build plain-English summary
        summary = self._summarise_graph(root_entity, nodes, edges)

        return {
            "root_entity": root_entity,
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "summary": summary,
        }

    # -----------------------------------------------------------------------
    # Division 7A Exposure Chain
    # -----------------------------------------------------------------------
    def find_div7a_exposure_chain(self) -> dict:
        """
        Identify all entities in the relationship graph that could be involved
        in a Division 7A exposure chain.

        Division 7A applies when:
          - A private company makes a loan/payment to a shareholder or associate.
          - The loan flows through related trusts or other companies.

        Returns:
          - companies: list of company entities in the graph
          - shareholders: list of individuals/entities who are shareholders
          - potential_exposures: list of {company, shareholder, relationship_path}
          - risk_summary: plain-English assessment
        """
        related = self.traverse_related_entities(depth=3)
        if "error" in related:
            return related

        nodes = related.get("nodes", [])
        edges = related.get("edges", [])

        companies = [n for n in nodes if n["type"] == "company"]
        trusts = [n for n in nodes if "trust" in n["type"]]
        shareholders = [
            e for e in edges
            if e["relationship_type"] in ("shareholder_of", "beneficiary_of", "director_of")
        ]

        potential_exposures = []
        for company in companies:
            for edge in shareholders:
                if edge["to_id"] == company["id"]:
                    # Find the shareholder node
                    shareholder_node = next(
                        (n for n in nodes if n["id"] == edge["from_id"]), None
                    )
                    if shareholder_node:
                        potential_exposures.append({
                            "company_id": company["id"],
                            "company_name": company["name"],
                            "shareholder_id": shareholder_node["id"],
                            "shareholder_name": shareholder_node["name"],
                            "relationship": edge["relationship_type"],
                        })

        risk_summary = self._build_div7a_risk_summary(companies, trusts, potential_exposures)

        return {
            "companies": companies,
            "trusts": trusts,
            "potential_exposures": potential_exposures,
            "exposure_count": len(potential_exposures),
            "risk_summary": risk_summary,
        }

    # -----------------------------------------------------------------------
    # Officer Cross-Entity Roles
    # -----------------------------------------------------------------------
    def get_officer_cross_entity_roles(self) -> dict:
        """
        Find all officers (directors, trustees, partners) who appear across
        multiple entities in this entity's relationship network.

        This is critical for:
          - Identifying common directors for related-party transaction checks.
          - Confirming trustee relationships for trust distribution planning.
          - Section 100A associate checks.

        Returns:
          - officers: list of {name, email, roles: [{entity_name, role}]}
          - cross_entity_officers: officers appearing in 2+ entities
          - summary: plain-English description
        """
        # First get all related entity IDs
        related = self.traverse_related_entities(depth=2)
        if "error" in related:
            return related

        entity_ids = [n["id"] for n in related.get("nodes", [])]
        if not entity_ids:
            return {"officers": [], "cross_entity_officers": [], "summary": "No related entities found."}

        # Query officers across all related entities
        placeholders = ",".join(["%s"] * len(entity_ids))
        sql = f"""
            SELECT
                eo.full_name,
                eo.email,
                eo.role,
                e.id::text AS entity_id,
                e.entity_name,
                e.entity_type
            FROM core_entityofficer eo
            JOIN core_entity e ON e.id = eo.entity_id
            WHERE eo.entity_id::text IN ({placeholders})
              AND e.is_archived = false
            ORDER BY eo.full_name, e.entity_name;
        """

        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, entity_ids)
                rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"Officer query failed: {e}")
            return self._error_result("Officer query failed")

        # Group by officer name
        officer_map = {}
        for full_name, email, role, entity_id, entity_name, entity_type in rows:
            key = full_name.lower().strip()
            if key not in officer_map:
                officer_map[key] = {
                    "name": full_name,
                    "email": email or "",
                    "roles": [],
                }
            officer_map[key]["roles"].append({
                "entity_id": entity_id,
                "entity_name": entity_name,
                "entity_type": entity_type,
                "role": role,
            })

        officers = list(officer_map.values())
        cross_entity = [o for o in officers if len(o["roles"]) > 1]

        summary = self._build_officer_summary(officers, cross_entity)

        return {
            "officers": officers,
            "cross_entity_officers": cross_entity,
            "total_officers": len(officers),
            "cross_entity_count": len(cross_entity),
            "summary": summary,
        }

    # -----------------------------------------------------------------------
    # Prior Year Findings
    # -----------------------------------------------------------------------
    def get_prior_year_findings(self, years_back: int = 3) -> dict:
        """
        Retrieve unresolved or historically significant EvaFindings for this
        entity across the last N financial years.

        Returns findings that the agent should be aware of when conducting
        the current year's review.
        """
        sql = """
            SELECT
                ef.id::text,
                ef.check_name,
                ef.title,
                ef.severity,
                ef.status,
                ef.explanation,
                ef.recommendation,
                fy.year_label,
                fy.end_date
            FROM core_evafinding ef
            JOIN core_evareview er ON er.id = ef.eva_review_id
            JOIN core_financialyear fy ON fy.id = er.financial_year_id
            WHERE fy.entity_id = %s::uuid
              AND fy.end_date >= NOW() - INTERVAL '%s years'
            ORDER BY fy.end_date DESC, ef.severity DESC
            LIMIT 50;
        """

        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, [self.entity_id, years_back])
                rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"Prior year findings query failed: {e}")
            return self._error_result("Prior year findings query failed")

        findings = []
        for row in rows:
            fid, check_name, title, severity, status, explanation, recommendation, year_label, end_date = row
            findings.append({
                "id": fid,
                "check_name": check_name,
                "title": title,
                "severity": severity,
                "status": status,
                "explanation": explanation or "",
                "recommendation": recommendation or "",
                "year_label": year_label,
                "year_end": str(end_date),
            })

        unresolved = [f for f in findings if f["status"] not in ("addressed", "closed")]
        recurring = self._find_recurring_findings(findings)

        return {
            "findings": findings,
            "unresolved_count": len(unresolved),
            "unresolved": unresolved,
            "recurring_issues": recurring,
            "total_count": len(findings),
            "summary": self._build_findings_summary(findings, unresolved, recurring),
        }

    # -----------------------------------------------------------------------
    # Helper Methods
    # -----------------------------------------------------------------------
    def _summarise_graph(self, root: dict, nodes: list, edges: list) -> str:
        """Build a plain-English summary of the entity relationship graph."""
        if len(nodes) <= 1:
            return f"{root['name']} has no related entities on file."

        related_names = [n["name"] for n in nodes if n["id"] != root["id"]]
        rel_types = list({e["label"] for e in edges})

        summary = (
            f"{root['name']} ({root['type'].replace('_', ' ').title()}) has "
            f"{len(nodes) - 1} related entit{'y' if len(nodes) == 2 else 'ies'}: "
            f"{', '.join(related_names[:5])}{'...' if len(related_names) > 5 else ''}. "
            f"Relationship types: {', '.join(rel_types)}."
        )
        return summary

    def _build_div7a_risk_summary(self, companies: list, trusts: list, exposures: list) -> str:
        """Build a plain-English Div7A risk summary."""
        if not exposures:
            return (
                f"No obvious Division 7A exposure chains detected. "
                f"Found {len(companies)} company entities and {len(trusts)} trust entities in the network."
            )
        exposure_descriptions = []
        for exp in exposures[:3]:
            exposure_descriptions.append(
                f"{exp['shareholder_name']} ({exp['relationship'].replace('_', ' ')}) "
                f"of {exp['company_name']}"
            )
        return (
            f"Potential Division 7A exposure detected: {len(exposures)} shareholder/associate "
            f"relationship(s) found. Key relationships: {'; '.join(exposure_descriptions)}. "
            f"Review loan accounts and UPEs for these relationships."
        )

    def _build_officer_summary(self, officers: list, cross_entity: list) -> str:
        """Build a plain-English officer summary."""
        if not officers:
            return "No officers found for this entity network."
        if not cross_entity:
            return (
                f"{len(officers)} officer(s) found across the entity network. "
                f"No officers appear in multiple entities."
            )
        cross_names = [o["name"] for o in cross_entity[:3]]
        return (
            f"{len(officers)} officer(s) found. {len(cross_entity)} officer(s) appear in "
            f"multiple entities: {', '.join(cross_names)}. "
            f"These individuals should be considered for related-party transaction checks."
        )

    def _find_recurring_findings(self, findings: list) -> list:
        """Identify findings that appear in multiple financial years."""
        from collections import Counter
        check_counts = Counter(f["check_name"] for f in findings)
        recurring_checks = {k for k, v in check_counts.items() if v >= 2}
        recurring = []
        seen = set()
        for f in findings:
            if f["check_name"] in recurring_checks and f["check_name"] not in seen:
                seen.add(f["check_name"])
                recurring.append({
                    "check_name": f["check_name"],
                    "title": f["title"],
                    "occurrences": check_counts[f["check_name"]],
                })
        return recurring

    def _build_findings_summary(self, findings: list, unresolved: list, recurring: list) -> str:
        """Build a plain-English findings summary."""
        if not findings:
            return "No prior Eva findings on record for this entity."
        parts = [f"{len(findings)} Eva finding(s) found across prior years."]
        if unresolved:
            parts.append(f"{len(unresolved)} remain unresolved.")
        if recurring:
            recurring_titles = [r["title"] for r in recurring[:2]]
            parts.append(f"Recurring issues: {', '.join(recurring_titles)}.")
        return " ".join(parts)

    def _error_result(self, message: str) -> dict:
        return {"error": message, "entity_id": self.entity_id}


# ---------------------------------------------------------------------------
# Convenience function for agent tool use
# ---------------------------------------------------------------------------
def query_client_graph(entity_id: str, depth: int = 2, include_div7a: bool = True,
                       include_officers: bool = True, include_prior_findings: bool = True) -> dict:
    """
    Convenience wrapper used by the Eva agent tool registry.

    Returns a comprehensive graph analysis dict combining all available
    graph queries for the given entity.
    """
    svc = GraphQueryService(entity_id=entity_id)

    result = {
        "entity_id": entity_id,
        "relationship_graph": svc.traverse_related_entities(depth=depth),
    }

    if include_div7a:
        result["div7a_exposure"] = svc.find_div7a_exposure_chain()

    if include_officers:
        result["officer_roles"] = svc.get_officer_cross_entity_roles()

    if include_prior_findings:
        result["prior_findings"] = svc.get_prior_year_findings()

    return result
