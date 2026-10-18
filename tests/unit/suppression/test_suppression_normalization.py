from uuid import uuid4

import pytest
from kb_contracts.enums import SuppressionReason, SuppressionScope
from kb_core.audit.service import OPERATOR
from kb_core.errors import EntityNotFound, InvalidInput
from kb_core.suppression.normalize import normalize_domain, normalize_email, normalize_value
from kb_core.suppression.service import SuppressionService, check_suppression
from kb_core.testing.fakes import FakeUowFactory


def test_email_normalisation():
    assert normalize_email("  Ed@Example.COM ") == "ed@example.com"


@pytest.mark.parametrize("bad", ["", "nope", "a@b", "a b@c.com", "@x.com"])
def test_bad_emails_are_rejected(bad):
    with pytest.raises(InvalidInput):
        normalize_email(bad)


@pytest.mark.parametrize(
    "raw",
    ["Example.com", "https://www.Blog.Example.com/a?b=1", "a@mail.example.com", "example.com."],
)
def test_domain_forms_converge_on_the_registered_domain(raw):
    assert normalize_domain(raw) == "example.com"


def test_contact_value_and_empty():
    assert normalize_value(SuppressionScope.CONTACT, " ABC ") == "abc"
    with pytest.raises(InvalidInput):
        normalize_value(SuppressionScope.CONTACT, " ")
    with pytest.raises(InvalidInput):
        normalize_domain("")


async def test_contact_scope_and_duplicate_add_is_idempotent_and_no_delete_api():
    f = FakeUowFactory()
    svc = SuppressionService()
    cid = uuid4()
    async with f() as uow:
        a = await svc.add(
            uow,
            scope=SuppressionScope.CONTACT,
            value=str(cid),
            reason=SuppressionReason.COMPLAINT,
            actor=OPERATOR,
            correlation_id="c",
        )
        b = await svc.add(
            uow,
            scope=SuppressionScope.CONTACT,
            value=str(cid).upper(),
            reason=SuppressionReason.COMPLAINT,
            actor=OPERATOR,
            correlation_id="c",
        )
        assert a.id == b.id
        assert (await check_suppression(uow, campaign_id=None, contact_id=cid)).suppressed
        assert not (await check_suppression(uow, campaign_id=None)).suppressed
    assert not [m for m in dir(svc) if "delete" in m or "remove" in m]


async def test_supersede_unknown_entry_is_not_found():
    f = FakeUowFactory()
    async with f() as uow:
        with pytest.raises(EntityNotFound):
            await SuppressionService().supersede(
                uow, entry_id=uuid4(), actor=OPERATOR, correlation_id="c", reason="x"
            )
