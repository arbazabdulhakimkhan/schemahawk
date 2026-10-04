"""Quality / eligibility classification tests."""
from __future__ import annotations

from schemahawk.models import EligibilityStatus, PipelineStatus, QualityStatus
from schemahawk.quality import classify

from conftest import make_job


def test_clean_job_is_ok():
    job = make_job()
    classify(job)
    assert job.quality_status == QualityStatus.OK
    assert job.status == PipelineStatus.DISCOVERED
    assert job.rejection_reason is None


def test_registration_fee_is_rejected():
    job = make_job(description="A small registration fee of $50 is required to start.")
    classify(job)
    assert job.quality_status == QualityStatus.REJECTED
    assert job.status == PipelineStatus.REJECTED
    assert "quality:" in job.rejection_reason


def test_crypto_deposit_is_rejected():
    job = make_job(description="Deposit your first payment in usdt to activate the account.")
    classify(job)
    assert job.quality_status == QualityStatus.REJECTED


def test_unrealistic_daily_earnings_is_rejected():
    job = make_job(description="Earn $900 per day from home with no experience needed.")
    classify(job)
    assert job.quality_status == QualityStatus.REJECTED


def test_soft_signal_is_suspicious_but_kept():
    job = make_job(description="Sign up bonus available for every new joiner.")
    classify(job)
    assert job.quality_status == QualityStatus.SUSPICIOUS
    assert job.status == PipelineStatus.DISCOVERED


def test_citizenship_requirement_is_restricted():
    job = make_job(description="US citizenship required for this role.")
    classify(job)
    assert job.eligibility_status == EligibilityStatus.RESTRICTED
    assert job.status == PipelineStatus.REJECTED
    assert "eligibility:" in job.rejection_reason


def test_restricted_can_be_kept_when_policy_disabled():
    job = make_job(description="No visa sponsorship available.")
    classify(job, reject_restricted=False)
    assert job.eligibility_status == EligibilityStatus.RESTRICTED
    assert job.status == PipelineStatus.DISCOVERED


def test_country_only_location_is_restricted():
    job = make_job(location="US only")
    classify(job)
    assert job.eligibility_status == EligibilityStatus.RESTRICTED


def test_remote_worldwide_is_eligible():
    job = make_job(location="Worldwide", remote=True)
    classify(job)
    assert job.eligibility_status == EligibilityStatus.ELIGIBLE


def test_missing_information_is_unknown_not_rejected():
    job = make_job(description=None, location=None, remote=None)
    classify(job)
    assert job.quality_status == QualityStatus.OK
    assert job.eligibility_status == EligibilityStatus.UNKNOWN
    assert job.status == PipelineStatus.DISCOVERED
