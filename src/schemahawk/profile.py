"""Candidate profile: the operator's own configuration (Phase 1).

Design rules, all enforced here rather than by convention:

- **Never inferred.** Every optional field is ``None``/``[]`` when absent. The
  profile is the *only* source of candidate facts; nothing is guessed from a
  resume, a job board or a language model.
- **Private by default.** ``config/profile.yaml`` is git-ignored. The committed
  ``config/profile.example.yaml`` holds generic placeholders only.
- **Not observable.** :meth:`CandidateProfile.summary` exposes counts and
  paths, never skill names, rates, contact details or employment history, so a
  profile can never leak through logs, reports or CI artifacts.
- **Safe to parse.** YAML is read with ``yaml.safe_load`` only - never
  ``yaml.load`` - so a profile file cannot construct arbitrary Python objects.
- **Forward compatible.** Unknown keys are preserved in ``extras`` instead
  of raising, so the schema can grow without breaking older profiles. Wrong
  *types* on known keys are still errors.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

PROFILE_VERSION = 1
DEFAULT_PROFILE_PATH = Path("config/profile.yaml")

# Provenance of a loaded profile. Lets the CLI say *which* profile is in use
# without disclosing its contents.
SOURCE_FILE = "file"
SOURCE_EXAMPLE = "example"
SOURCE_DEFAULT = "default"


class ProfileError(ValueError):
    """The profile could not be loaded or failed validation.

    The message names the offending key but never echoes the file body, so a
    malformed private profile cannot leak into logs or CI output.
    """


@dataclass(frozen=True)
class Skill:
    """One declared skill.

    ``proficiency``, ``years`` and ``last_used`` stay ``None`` unless the
    operator supplied them; they are never estimated.
    """

    name: str
    category: str | None = None
    proficiency: str | None = None
    years: float | None = None
    last_used: str | None = None



@dataclass(frozen=True)
class CandidateProfile:
    """Structured candidate configuration. Every field may be ``None``/empty."""

    version: int = PROFILE_VERSION

    # --- identity ---
    name: str | None = None
    contact_email: str | None = None
    headline: str | None = None

    # --- experience ---
    total_years_experience: float | None = None
    seniority: str | None = None
    availability: str | None = None
    timezone: str | None = None
    work_authorization: str | None = None

    # --- skills & history ---
    skills: tuple[Skill, ...] = ()
    employment_history: tuple[dict[str, Any], ...] = ()
    projects: tuple[dict[str, Any], ...] = ()
    certifications: tuple[dict[str, Any], ...] = ()
    education: tuple[dict[str, Any], ...] = ()
    languages: tuple[dict[str, Any], ...] = ()

    # --- preferences ---
    preferred_contract_types: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    remote_preference: str | None = None
    preferred_timezones: tuple[str, ...] = ()
    preferred_rate: dict[str, Any] | None = None
    preferences: dict[str, Any] = field(default_factory=dict)

    # --- provenance / forward compatibility ---
    source: str = SOURCE_DEFAULT
    path: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    # --- safe introspection -------------------------------------------------
    @property
    def skill_names(self) -> tuple[str, ...]:
        """Skill names as written. Used for matching, never for reporting."""
        return tuple(skill.name for skill in self.skills)

    @property
    def is_populated(self) -> bool:
        """True when the profile carries operator-supplied information."""
        return bool(self.skills or self.total_years_experience is not None
                    or self.preferred_locations or self.preferred_contract_types)

    def summary(self) -> dict[str, str]:
        """Printable summary safe for logs, reports and CI artifacts.

        Only counts, the source and the file path are exposed. Skill names,
        rates, contact details and history are deliberately omitted.
        """
        return {
            "source": self.source,
            "path": self.path or "(built-in default)",
            "version": str(self.version),
            "skills": str(len(self.skills)),
            "years experience": (
                # Integral years read as "8", not "8.0" (this is operator-facing).
                str(int(self.total_years_experience))
                if self.total_years_experience is not None
                and float(self.total_years_experience).is_integer()
                else (
                    str(self.total_years_experience)
                    if self.total_years_experience is not None
                    else "not specified"
                )
            ),
            "seniority": self.seniority or "not specified",
            "contract types": str(len(self.preferred_contract_types)),
            "locations": str(len(self.preferred_locations)),
            "remote": self.remote_preference or "not specified",
            "rate": "set" if self.preferred_rate else "not specified",
            "history entries": str(len(self.employment_history)),
            "populated": "yes" if self.is_populated else "no (empty profile)",
        }


# --- coercion helpers -------------------------------------------------------

def _opt_str(value: Any, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileError(f"'{key}' must be a string, got {type(value).__name__}")
    text = value.strip()
    return text or None


def _opt_float(value: Any, key: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"'{key}' must be a number, got {type(value).__name__}")
    if value < 0:
        raise ProfileError(f"'{key}' must not be negative")
    return float(value)


def _str_tuple(value: Any, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ProfileError(f"'{key}' must be a list, got {type(value).__name__}")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ProfileError(f"'{key}[{index}]' must be a string, "
                               f"got {type(item).__name__}")
        text = item.strip()
        if text:
            out.append(text)
    return tuple(out)


def _dict_tuple(value: Any, key: str) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ProfileError(f"'{key}' must be a list, got {type(value).__name__}")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ProfileError(f"'{key}[{index}]' must be a mapping, "
                               f"got {type(item).__name__}")
        out.append(dict(item))
    return tuple(out)


def _opt_mapping(value: Any, key: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProfileError(f"'{key}' must be a mapping, got {type(value).__name__}")
    return dict(value)


def _parse_skills(value: Any) -> tuple[Skill, ...]:
    """Accept either a list of mappings or a list of plain skill names."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ProfileError(f"'skills' must be a list, got {type(value).__name__}")

    skills: list[Skill] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            # Shorthand: a bare name means "declared, nothing else known".
            name = item.strip()
            if name:
                skills.append(Skill(name=name))
            continue
        if not isinstance(item, dict):
            raise ProfileError(
                f"'skills[{index}]' must be a mapping or a string, "
                f"got {type(item).__name__}")
        name = _opt_str(item.get("name"), f"skills[{index}].name")
        if not name:
            raise ProfileError(f"'skills[{index}].name' is required")
        skills.append(Skill(
            name=name,
            category=_opt_str(item.get("category"), f"skills[{index}].category"),
            proficiency=_opt_str(item.get("proficiency"),
                                 f"skills[{index}].proficiency"),
            years=_opt_float(item.get("years"), f"skills[{index}].years"),
            last_used=_opt_str(item.get("last_used"), f"skills[{index}].last_used"),
        ))
    return tuple(skills)


# Keys the loader understands; anything else is preserved in ``extras``.
_KNOWN_KEYS = frozenset({
    "version", "candidate", "total_years_experience", "seniority",
    "availability", "timezone", "work_authorization", "skills",
    "employment_history", "projects", "certifications", "education",
    "languages", "preferred_contract_types", "preferred_locations",
    "remote_preference", "preferred_timezones", "preferred_rate",
    "preferences",
})


def parse_profile(data: Any, *, source: str = SOURCE_FILE,
                  path: str | None = None) -> CandidateProfile:
    """Validate a parsed YAML mapping into a :class:`CandidateProfile`.

    Raises :class:`ProfileError` for structural problems and unknown versions.
    """
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ProfileError(
            f"profile must be a mapping at the top level, got {type(data).__name__}")

    version = data.get("version", PROFILE_VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ProfileError(f"'version' must be an integer, got {type(version).__name__}")
    if version > PROFILE_VERSION:
        raise ProfileError(
            f"profile version {version} is newer than supported version "
            f"{PROFILE_VERSION}; upgrade schemahawk")

    candidate = data.get("candidate") or {}
    if not isinstance(candidate, dict):
        raise ProfileError(
            f"'candidate' must be a mapping, got {type(candidate).__name__}")

    extras = {key: value for key, value in data.items() if key not in _KNOWN_KEYS}
    if extras:
        # Log key names only: the values may be private.
        log.debug("profile has %d unrecognized field(s): %s",
                  len(extras), ", ".join(sorted(extras)))

    return CandidateProfile(
        version=version,
        name=_opt_str(candidate.get("name"), "candidate.name"),
        contact_email=_opt_str(candidate.get("contact_email"),
                               "candidate.contact_email"),
        headline=_opt_str(candidate.get("headline"), "candidate.headline"),
        total_years_experience=_opt_float(
            data.get("total_years_experience"), "total_years_experience"),
        seniority=_opt_str(data.get("seniority"), "seniority"),
        availability=_opt_str(data.get("availability"), "availability"),
        timezone=_opt_str(data.get("timezone"), "timezone"),
        work_authorization=_opt_str(data.get("work_authorization"),
                                    "work_authorization"),
        skills=_parse_skills(data.get("skills")),
        employment_history=_dict_tuple(data.get("employment_history"),
                                       "employment_history"),
        projects=_dict_tuple(data.get("projects"), "projects"),
        certifications=_dict_tuple(data.get("certifications"), "certifications"),
        education=_dict_tuple(data.get("education"), "education"),
        languages=_dict_tuple(data.get("languages"), "languages"),
        preferred_contract_types=_str_tuple(data.get("preferred_contract_types"),
                                            "preferred_contract_types"),
        preferred_locations=_str_tuple(data.get("preferred_locations"),
                                       "preferred_locations"),
        remote_preference=_opt_str(data.get("remote_preference"),
                                   "remote_preference"),
        preferred_timezones=_str_tuple(data.get("preferred_timezones"),
                                       "preferred_timezones"),
        preferred_rate=_opt_mapping(data.get("preferred_rate"), "preferred_rate"),
        preferences=_opt_mapping(data.get("preferences"), "preferences") or {},
        source=source,
        path=path,
        extras=extras,
    )


def load_profile(path: str | Path | None = None, *,
                 source: str = SOURCE_FILE) -> CandidateProfile:
    """Read and validate a YAML profile from an **explicitly given** ``path``.

    Uses ``yaml.safe_load``: a profile file can never instantiate arbitrary
    Python objects. Raises :class:`ProfileError` with a precise message on
    malformed or missing input.

    ``path`` must be explicit. It used to default to ``DEFAULT_PROFILE_PATH``,
    which made ``load_profile()`` resolve to the operator's private
    ``config/profile.yaml`` on a developer machine while raising ``ProfileError``
    in CI where that file is (correctly) absent - the same call behaving
    differently in two places. The three profile concepts now have one
    unambiguous entry point each:

    - this function, for a path the caller names;
    - :func:`load_private_profile`, for the operator's default local file;
    - :func:`default_profile`, for a guaranteed-empty profile.
    """
    if path is None:
        raise ProfileError(
            "load_profile() requires an explicit path. Use "
            "load_private_profile() for the default local profile.yaml, "
            "load_profile_or_default() to fall back to an empty profile, "
            "or default_profile() for a guaranteed-empty profile."
        )
    target = Path(path)
    if not target.exists():
        raise ProfileError(f"no profile file at {target}")
    if target.is_dir():
        raise ProfileError(f"profile path {target} is a directory, not a file")

    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(
            f"cannot read profile at {target}: {exc.strerror}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        # Line/column only: never echo the document body.
        raise ProfileError(
            f"invalid YAML in {target}: {exc.__class__.__name__}") from exc

    return parse_profile(data, source=source, path=str(target))


def default_profile() -> CandidateProfile:
    """Empty built-in profile.

    Used when no profile file exists - notably in CI, where the private
    ``config/profile.yaml`` is (correctly) absent from the checkout.
    """
    return CandidateProfile(source=SOURCE_DEFAULT, path=None)


def example_profile() -> CandidateProfile:
    """Load the committed example template."""
    return load_profile(Path(__file__).resolve().parents[2]
                        / "config" / "profile.example.yaml",
                        source=SOURCE_EXAMPLE)


def load_profile_or_default(path: str | Path | None = None) -> CandidateProfile:
    """Load ``path`` if it exists, otherwise return :func:`default_profile`."""
    target = Path(path) if path is not None else DEFAULT_PROFILE_PATH
    if not target.exists():
        log.info("no profile at %s; using empty default profile", target)
        return default_profile()
    return load_profile(target)


def load_private_profile(
    path: str | Path | None = None,
) -> CandidateProfile:
    """The operator's default local profile, or an empty profile if absent.

    This is the only function that resolves :data:`DEFAULT_PROFILE_PATH` on its
    own. Naming it makes "use my real profile" an explicit decision at the call
    site, so a test or analysis script cannot pick up personal data by accident.
    """
    return load_profile_or_default(path)


def profile_from_settings(settings) -> CandidateProfile:
    """Resolve the profile through ``Settings.candidate_profile_path``."""
    return load_profile_or_default(getattr(settings, "candidate_profile_path", None))


__all__ = [
    "PROFILE_VERSION",
    "SOURCE_DEFAULT",
    "SOURCE_EXAMPLE",
    "SOURCE_FILE",
    "CandidateProfile",
    "ProfileError",
    "Skill",
    "default_profile",
    "example_profile",
    "load_private_profile",
    "load_profile",
    "load_profile_or_default",
    "parse_profile",
    "profile_from_settings",
]

