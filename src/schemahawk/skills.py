"""Canonical skill vocabulary for deterministic job-skill extraction.

This module is the single source of truth for which skills a posting may be
asked to have. A flat list of strings cannot carry the two things that matter
as the list grows:

- a **category**, so coverage can be reasoned about (cloud vs. BI vs. streaming);
- **aliases**, so a posting saying "s3" or "postgres" resolves to the same
  canonical skill as "Amazon S3" or "PostgreSQL".

Deliberate rules, all enforced by ``tests/test_skills.py``:

1. **No bare ambiguous terms.** ``quality``, ``api``, ``pipeline``,
   ``integration``, ``segment`` and ``R`` are excluded on purpose. Token-boundary
   matching cannot save them: "R&D" contains the token ``R``, "segmentation"
   contains ``segment``, and "quality time" contains ``quality``. Requiring a
   qualified phrase (``Data governance``, ``REST APIs``) costs a little recall
   and buys correctness. Inflating coverage with false positives is worse than
   being blind to a term.
2. **One canonical name per skill.** An alias is never a separate entry, so a
   posting is never counted twice for the same technology.
3. **Extends, never replaces.** The original 37 skills keep their exact canonical
   names, so existing profile skills and stored jobs stay valid.

Operators extend the vocabulary for their own market through
``preferences.extra_skills`` in their git-ignored ``config/profile.yaml``; see
:func:`vocabulary_for`. The built-in default always applies, so matching never
silently weakens because a file is missing - which matters in CI, where the
private profile is correctly absent.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class SkillSpec:
    """One canonical skill with its aliases and category."""

    canonical: str
    category: str
    aliases: tuple[str, ...] = ()

# --- categories (constants so tests never depend on a bare string) ----------
CORE = "core-language"
DATABASE = "database"
WAREHOUSE = "data-warehouse"
CLOUD = "cloud-platform"
CLOUD_SERVICE = "cloud-service"
ORCHESTRATION = "orchestration"
STREAMING = "streaming"
DISTRIBUTED = "distributed-processing"
BI = "bi-visualization"
TRANSFORM = "data-transform"
DATA_QUALITY = "data-quality-governance"
DATA_MGMT = "data-management-systems"
ANALYTICS_TOOL = "analytics-tooling"
INTEGRATION = "integration-api"
DEVOPS = "devops-data-infra"
ML = "machine-learning"

# --- the vocabulary ---------------------------------------------------------
#
# Grouped by category for readability; matching does not depend on order.
# Aliases are lowercase and must be unambiguous across the whole table.
DEFAULT_VOCABULARY: tuple[SkillSpec, ...] = (
    # --- core languages (all pre-existing) ---
    SkillSpec("SQL", DATABASE),
    SkillSpec("Python", CORE),
    SkillSpec("Java", CORE),
    SkillSpec("Scala", DISTRIBUTED),

    # --- databases ---
    SkillSpec("Azure SQL", DATABASE),
    SkillSpec("PostgreSQL", DATABASE, ("postgres", "psql",)),
    SkillSpec("MySQL", DATABASE, ("mariadb",)),
    SkillSpec("Oracle Database", DATABASE, ("oracle db",)),
    SkillSpec("SQL Server", DATABASE, ("mssql",)),
    SkillSpec("IBM Db2", DATABASE, ("db2",)),

    # --- data warehouses ---
    SkillSpec("Snowflake", WAREHOUSE),
    SkillSpec("Google BigQuery", WAREHOUSE, ("bigquery", "bq",)),
    SkillSpec("Redshift", WAREHOUSE, ("amazon redshift",)),
    SkillSpec("Synapse", WAREHOUSE, ("azure synapse", "synapse analytics",)),
    SkillSpec("Databricks", WAREHOUSE, ("databricks lakehouse",)),
    SkillSpec("Teradata", WAREHOUSE),
    SkillSpec("Hadoop", DISTRIBUTED),
    SkillSpec("Hive", WAREHOUSE, ("apache hive", "hiveql",)),

    # --- cloud platforms ---
    SkillSpec("AWS", CLOUD),
    SkillSpec("Azure", CLOUD),
    SkillSpec("GCP", CLOUD, ("google cloud", "google cloud platform",)),

    # --- cloud services ---
    SkillSpec("Amazon S3", CLOUD_SERVICE, ("s3", "aws s3",)),
    SkillSpec("AWS Glue", CLOUD_SERVICE, ("glue",)),
    SkillSpec("AWS Athena", CLOUD_SERVICE, ("athena",)),
    SkillSpec("AWS Lambda", CLOUD_SERVICE, ("lambda",)),
    SkillSpec("Amazon Kinesis", CLOUD_SERVICE, ("kinesis",)),
    SkillSpec("Azure Data Lake", CLOUD_SERVICE, ("data lake", "adls",)),
    SkillSpec("Google Cloud Storage", CLOUD_SERVICE, ("gcs", "cloud storage",)),

    # --- orchestration ---
    SkillSpec("Azure Data Factory (ADF)", ORCHESTRATION, ("azure data factory", "adf",)),
    SkillSpec("Airflow", ORCHESTRATION, ("apache airflow",)),
    SkillSpec("dbt", ORCHESTRATION),
    SkillSpec("Dagster", ORCHESTRATION),
    SkillSpec("Prefect", ORCHESTRATION),
    SkillSpec("Apache NiFi", ORCHESTRATION, ("nifi",)),
    SkillSpec("Airbyte", ORCHESTRATION),
    SkillSpec("Fivetran", ORCHESTRATION),

    # --- streaming ---
    SkillSpec("Kafka", STREAMING),
    SkillSpec("Kafka Streams", STREAMING),
    SkillSpec("Apache Flink", STREAMING, ("flink",)),
    SkillSpec("Structured Streaming", STREAMING, ("spark structured streaming",)),

    # --- distributed processing ---
    SkillSpec("Spark", DISTRIBUTED, ("apache spark",)),
    SkillSpec("PySpark", DISTRIBUTED),
    SkillSpec("Apache Presto", DISTRIBUTED, ("presto",)),
    SkillSpec("Trino", DISTRIBUTED),
    SkillSpec("Apache HBase", DISTRIBUTED, ("hbase",)),

    # --- ETL / ELT / transform ---
    SkillSpec("ETL / ELT", TRANSFORM, ("etl", "elt", "etl/elt",)),
    SkillSpec("Informatica", TRANSFORM),
    SkillSpec("SSIS", TRANSFORM),
    SkillSpec("Talend", TRANSFORM),
    SkillSpec("Data Modeling", TRANSFORM, ("data modelling", "dimensional modeling",)),
    SkillSpec("Data extraction and transformation", TRANSFORM, ("data extraction",)),
    SkillSpec("Data pipelines", TRANSFORM, ("data pipeline",)),

    # --- BI / visualization ---
    SkillSpec("Power BI", BI, ("powerbi",)),
    SkillSpec("Power Query", BI),
    SkillSpec("Tableau Desktop", BI, ("tableau",)),
    SkillSpec("Tableau Server", BI),
    SkillSpec("DAX", BI),
    SkillSpec("Looker", BI),
    SkillSpec("Qlik", BI, ("qlikview", "qliksense",)),
    SkillSpec("IBM Cognos", BI, ("cognos",)),
    SkillSpec("Domo", BI),
    SkillSpec("MicroStrategy", BI, ("microstrategy",)),
    SkillSpec("Spotfire", BI, ("tableau spotfire",)),
    SkillSpec("Dashboard development", BI, ("dashboards",)),

    # --- analytics tooling ---
    SkillSpec("Alteryx Designer", ANALYTICS_TOOL, ("alteryx",)),
    SkillSpec("Excel", ANALYTICS_TOOL, ("microsoft excel",)),

    # --- data quality / governance ---
    SkillSpec("Data quality / QA", DATA_QUALITY, ("data quality", "data quality checks",)),
    SkillSpec("Data governance", DATA_QUALITY, ("data governance framework",)),
    SkillSpec("Data catalog", DATA_QUALITY, ("data catalogues", "metadata catalog",)),
    SkillSpec("Data lineage", DATA_QUALITY, ("lineage tracking",)),
    SkillSpec("Great Expectations", DATA_QUALITY, ("great expectations library",)),

    # --- data management / customer data ---
    SkillSpec("Salesforce", DATA_MGMT),
    SkillSpec("SAP", DATA_MGMT, ("sap erp",)),
    SkillSpec("Shopify", DATA_MGMT),
    SkillSpec("Amplitude", DATA_MGMT),
    SkillSpec("Mixpanel", DATA_MGMT),

    # --- integration / APIs ---
    SkillSpec("REST APIs", INTEGRATION, ("rest api", "restful",)),
    SkillSpec("Jira REST API", INTEGRATION, ("jira api",)),
    SkillSpec("GraphQL", INTEGRATION, ("graph ql",)),
    SkillSpec("gRPC", INTEGRATION),
    SkillSpec("Webhooks", INTEGRATION, ("web hook",)),
    SkillSpec("OpenAPI", INTEGRATION, ("swagger",)),

    # --- DevOps / data infrastructure ---
    SkillSpec("GitHub", DEVOPS),
    SkillSpec("Docker", DEVOPS, ("docker containers",)),
    SkillSpec("Kubernetes", DEVOPS, ("k8s",)),
    SkillSpec("Terraform", DEVOPS),
    SkillSpec("GitHub Actions", DEVOPS, ("github action",)),
)

# --- vocabulary assembly ----------------------------------------------------

def canonical_names(vocabulary: tuple[SkillSpec, ...] = DEFAULT_VOCABULARY
                    ) -> tuple[str, ...]:
    """Canonical names only, in vocabulary order (what extraction scans for)."""
    return tuple(spec.canonical for spec in vocabulary)

def vocabulary_for(profile=None) -> tuple[SkillSpec, ...]:
    """Built-in vocabulary, optionally extended by the operator's own skills.

    ``profile.preferences.extra_skills`` accepts names or
    ``{name, category, aliases}`` mappings. Unknown or malformed entries are
    ignored rather than fatal, and the built-in vocabulary always applies first,
    so a missing or odd profile can never weaken matching.
    """
    extra: list[SkillSpec] = []
    preferences = getattr(profile, "preferences", None)
    raw = preferences.get("extra_skills") if isinstance(preferences, dict) else None

    if isinstance(raw, (list, tuple)):
        known = {spec.canonical.lower() for spec in DEFAULT_VOCABULARY}
        for item in raw:
            if isinstance(item, str):
                name, category, aliases = item.strip(), "extra", ()
            elif isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                category = str(item.get("category") or "extra").strip() or "extra"
                supplied = item.get("aliases") or ()
                aliases = tuple(a.strip() for a in supplied
                                if isinstance(a, str) and a.strip()) \
                    if isinstance(supplied, (list, tuple)) else ()
            else:
                continue
            # A name the built-in vocabulary already covers is not re-added: it
            # would count one technology twice.
            if not name or name.lower() in known:
                continue
            known.add(name.lower())
            extra.append(SkillSpec(name, category, aliases))

    return DEFAULT_VOCABULARY + tuple(extra)

__all__ = [
    "SkillSpec",
    "DEFAULT_VOCABULARY",
    "CORE", "DATABASE", "WAREHOUSE", "CLOUD", "CLOUD_SERVICE",
    "ORCHESTRATION", "STREAMING", "DISTRIBUTED", "BI", "TRANSFORM",
    "DATA_QUALITY", "DATA_MGMT", "ANALYTICS_TOOL", "INTEGRATION", "DEVOPS", "ML",
    "alias_map",
    "canonical_names",
    "vocabulary_for",
]

def alias_map(vocabulary: tuple[SkillSpec, ...] = DEFAULT_VOCABULARY
              ) -> dict[str, str]:
    """Map every alias (lowercased) to its canonical name.

    An alias that collides with another skill's canonical name is skipped rather
    than silently redirecting, so one skill can never swallow another.
    """
    canonicals = {spec.canonical.lower() for spec in vocabulary}
    mapping: dict[str, str] = {}
    for spec in vocabulary:
        for alias in spec.aliases:
            key = alias.lower().strip()
            if key and key not in canonicals and key not in mapping:
                mapping[key] = spec.canonical
    return mapping

