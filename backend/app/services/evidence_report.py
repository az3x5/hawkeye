"""Build a BlackGlass-compatible report from committed, cited evidence."""

from __future__ import annotations

from typing import Any

SECTIONS = [
    ("osp-cover-meta", "Report Details", "ރިޕޯޓްގެ ތަފްޞީލު"),
    ("osp-profile-summary", "Executive Summary", "ޕްރޮފައިލް ޚުލާސާ"),
    ("osp-key-findings", "Key Findings", "މުހިންމު ހޯދުންތައް"),
    ("osp-figure-photos", "Subject Images", "މައުޟޫޢުގެ ފޮޓޯ"),
    ("osp-identity-photo", "Identity & Photo Assessment", "ވަނަވަރާއި ފޮޓޯ އެސެސްމަންޓް"),
    ("osp-image-integrity", "Image Integrity & Manipulation Review", "ފޮޓޯގެ ސައްޙަކަން ބެލުން"),
    ("osp-platform-snapshot", "Platform Snapshot", "ޕްލެޓްފޯމް ޚުލާސާ"),
    ("osp-username-evolution", "Username & Identity Evolution", "ޔޫޒަރނޭމް ބަދަލުވި ގޮތް"),
    ("osp-public-info", "Publicly Identifiable Information", "ޢާންމުކޮށް ފެންނަ މަޢުމާތު"),
    ("osp-figure-behaviour", "Observed Behavioural Tendencies", "ފެންނަ އުޅުމުގެ ސިފަތައް"),
    ("osp-behaviour-pattern", "Behavioural Pattern", "އުޅުމުގެ ސިފަ"),
    ("osp-routines", "Daily Routines & Habits", "ދުވަހީ އާދަތައް"),
    ("osp-communication-style", "Communication Style", "މުޢާމަލާތުކުރާ ގޮތް"),
    ("osp-decision-risk", "Decision-Making & Risk", "ނިންމުންތަކާއި ނުރައްކާ"),
    ("osp-triggers", "Emotional & Contextual Triggers", "އަސަރުކުރާ ކަންތައްތައް"),
    ("osp-privacy-contradictions", "Privacy Contradictions", "ޕްރައިވަސީ ފުށުއެރުން"),
    ("osp-behaviour-timeline", "Behaviour Change Timeline", "އުޅުން ބަދަލުވި ތާރީޚް"),
    ("osp-analytical-cautions", "Analytical Cautions", "ސަމާލުކަންދޭންވީ ކަންތައް"),
    ("osp-figure-associates", "Subject and Close Associates", "މައުޟޫޢާއި ގާތް ފަރާތްތައް"),
    ("osp-associates", "Associates & Online Circles", "ގުޅުންހުރި ފަރާތްތަކާއި ސާރކަލްތައް"),
    ("osp-associate-detail", "Associate — Name & Role", "ގުޅުންހުރި ފަރާތް"),
    ("osp-interpreting-association", "Interpreting Association", "ގުޅުން މާނަކުރުން"),
    ("osp-data-exposure", "Data-Exposure Record", "މަޢުލޫމާތު ބޭޒާރުވުން"),
    ("osp-risky-behaviour", "Risky or Negligent Security Behaviour", "ސެކިއުރިޓީގައި އިހުމާލު"),
    ("osp-crypto-footprint", "Cryptocurrency Footprint", "ކްރިޕްޓޯ ފުޓްޕްރިންޓް"),
    ("osp-screening", "Suspicious-Activity Screening", "ޝައްކުކުރެވޭ ޙަރަކާތް ބެލުން"),
    ("osp-indicators-not-found", "Serious Indicators Checked but Not Found", "ބެލި ނަމަވެސް ނުފެނުނު ކަންތައް"),
    ("osp-integrated-profile", "Integrated Profile", "ޖުމްލަ ޕްރޮފައިލް"),
    ("osp-confidence-gaps", "Confidence & Gaps", "ޔަޤީންކަމާއި ފަޅުކަން"),
    ("osp-photo-catalogue", "Photo Catalogue & Visual-Evidence Log", "ފޮޓޯ ލޮގު"),
    ("osp-platform-matrix", "Selected Platform Matrix", "ޕްލެޓްފޯމް މެޓްރިކްސް"),
]


def localized(en: str, dv: str = "") -> dict[str, str]:
    """Return the localized-cell shape used by BlackGlass report exports."""
    return {"en": en, "dv": dv}


def build_blackglass_report(analysis: dict[str, Any]) -> dict[str, Any]:
    """Create a deterministic report; never add conclusions absent from the result."""
    legacy_source = analysis.get("source") or {}
    source_id = analysis.get("source_id") or legacy_source.get("object_id")
    source_type = analysis.get("source_type") or legacy_source.get("object_type")
    attributes = dict(analysis.get("attributes") or {})
    if legacy_source:
        attributes.setdefault("source_system", legacy_source.get("system"))
        attributes.setdefault("collected_at", legacy_source.get("collected_at"))
    subject = analysis.get("subject") or {}
    subject_id = subject.get("subject_id", source_id)
    subject_type = subject.get("subject_type", source_type)
    subject_label = subject.get("display_label") or subject_id
    evidence = analysis.get("evidence", [])
    findings = analysis.get("findings", [])
    contradictions = analysis.get("contradictions", [])
    warnings = analysis.get("warnings", [])
    analysis_id = str(analysis["analysis_id"])
    generated_at = analysis["updated_at"].isoformat()
    evidence_refs = [
        {
            "evidenceId": str(item["evidence_id"]),
            "kind": item["kind"],
            "quote": item["original_text"],
            "locator": item["locator"],
            "provenance": item["provenance"],
            "reviewStatus": item.get("review_status", "unreviewed"),
        }
        for item in evidence
    ]
    by_section: dict[str, list[dict[str, Any]]] = {}
    for item in findings + contradictions:
        by_section.setdefault(item.get("section", "osp-key-findings"), []).append(item)
    gaps = [f"{item['stage']}: {item['code']}" for item in warnings]
    unsupported = "Insufficient cited evidence for a supported conclusion."

    def finding_rows(items: list[dict[str, Any]]) -> list[list[dict[str, str]]]:
        """Render only validated findings and keep their evidence IDs visible."""
        return [
            [
                localized(item["statement"]),
                localized(f"Confidence: {item.get('confidence', 0.5):.2f}"),
                localized(item.get("basis", "explicit")),
                localized(
                    ", ".join(str(citation["evidence_id"]) for citation in item["citations"])
                ),
            ]
            for item in items
        ]

    blocks = []
    for index, (kind, en, dv) in enumerate(SECTIONS, start=1):
        body = unsupported
        rows: list[list[dict[str, str]]] = []
        if kind == "osp-cover-meta":
            body = "Cyber-ai evidence analysis report. Findings remain unreviewed."
            rows = [
                [localized("Analysis ID"), localized(analysis_id)],
                [localized("Source type"), localized(str(source_type))],
                [localized("Source record"), localized(str(source_id))],
                [localized("Status"), localized(analysis["status"])],
            ]
        elif kind == "osp-profile-summary":
            evidence_label = "section" if len(evidence) == 1 else "sections"
            findings_label = "finding" if len(findings) == 1 else "findings"
            contradictions_label = (
                "contradiction" if len(contradictions) == 1 else "contradictions"
            )
            contradictions_verb = "was" if len(contradictions) == 1 else "were"
            body = (
                f"Executive summary based on {len(evidence)} retained evidence {evidence_label} "
                f"and {len(findings)} citation-validated {findings_label}. "
                f"{len(contradictions)} {contradictions_label} {contradictions_verb} retained. "
                "This is unreviewed analytical output, not a verified factual determination."
            )
            rows = finding_rows((findings + contradictions)[:5])
            if not rows:
                rows = [[localized(unsupported)]]
        elif kind == "osp-key-findings":
            body = "Highest-priority citation-validated observations across the report."
            rows = finding_rows((findings + contradictions)[:10])
            if not rows:
                rows = [[localized(unsupported)]]
        elif kind in by_section:
            body = "Post-derived, cited observations generated by cyber-ai."
            rows = finding_rows(by_section[kind])
        elif kind == "osp-analytical-cautions":
            body = "Citations establish source linkage, not factual truth or semantic entailment."
            rows = [[localized(gap)] for gap in gaps]
        elif kind == "osp-confidence-gaps":
            body = "Unreviewed output; inspect retained originals before relying on conclusions."
            rows = [[localized(gap)] for gap in gaps] or [[localized(unsupported)]]
        elif kind == "osp-photo-catalogue":
            visual = [item for item in evidence if item["kind"] == "visual_observation"]
            body = "Sampled visual observations; unsampled content is not represented."
            rows = [
                [localized(str(item["evidence_id"])), localized(item["original_text"])]
                for item in visual
            ]
        blocks.append(
            {
                "id": f"{analysis_id}-{index}",
                "type": kind,
                "heading": localized(en, dv),
                "body": localized(body),
                "rows": rows,
            }
        )
    outline = [
        {
            "id": block["id"],
            "blockKey": block["type"],
            "kind": "section",
            "title": block["heading"],
            "enabled": True,
            "collapsed": False,
            "status": "ready",
        }
        for block in blocks
    ]
    machine_findings = [{**item, "kind": "finding"} for item in findings] + [
        {**item, "kind": "contradiction"} for item in contradictions
    ]
    return {
        "message": "Report generated successfully.",
        "data": {
            "id": analysis_id,
            "reference": f"EE/{analysis_id[:8]}",
            "reference_prefix": "EE",
            "reference_serial": analysis_id[:8],
            "reference_year": analysis["updated_at"].year,
            "subject": subject_label,
            "subject_id": subject_id,
            "subject_type": subject_type,
            "lang": "en",
            "status": "draft",
            "generation_status": "ready"
            if analysis["status"] in {"completed", "partial"}
            else analysis["status"],
            "last_error": None,
            "version": analysis["revision"],
            "template": "eagleeye-evidence-v1",
            "template_version": "1.0",
            "created_at": analysis["created_at"].isoformat(),
            "updated_at": generated_at,
            "generated_at": generated_at,
            "created_by_id": "cyber-ai",
            "metadata": {
                "content_items": len(evidence),
                "linked_profiles": 0,
                "gaps": gaps,
                "subject_platform": attributes.get("source_system"),
                "observation_from": attributes.get("collected_at"),
                "observation_to": attributes.get("collected_at"),
                "cover_profile_image_url": None,
                "generation_options": {"citation_required": True},
                "generator": {"system": "cyber-ai", "pipeline": "evidence_analysis"},
                "report_request_id": analysis.get("report_request_id"),
            },
            "document": {
                "schema": 2,
                "version": {"stage": "draft", "number": "0.1"},
                "lang": "en",
                "cover": {
                    "date": generated_at,
                    "focus": source_type,
                    "purpose": "Evidence analysis",
                    "subject": subject_label,
                    "version": str(analysis["revision"]),
                    "profileImage": "",
                    "observationFrom": attributes.get("collected_at"),
                    "observationTo": attributes.get("collected_at"),
                },
                "outline": outline,
                "content": {"lexical": {"root": {"type": "root", "children": []}}},
                "evidenceRefs": evidence_refs,
                "mediaRefs": [],
                "findings": machine_findings,
            },
            "blocks": blocks,
        },
    }
