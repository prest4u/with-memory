from __future__ import annotations

import uuid
from unittest.mock import patch

from eric_memory.errors import ConflictError, ContentRejectedError, NotFoundError, PermissionRequiredError
from eric_memory.permissions import FACT_SEARCH, FACT_WRITE, HISTORY_READ
from eric_memory.service import MemoryService
from tests.helpers import TempServiceTest


class CandidateAndAclTests(TempServiceTest):
    def setUp(self) -> None:
        super().setUp()
        self.h1_root = self.data_dir / "h1-source"
        self.h2_root = self.data_dir / "h2-source"
        self.h1_root.mkdir()
        self.h2_root.mkdir()
        (self.h1_root / "one.md").write_text("one", encoding="utf-8")
        (self.h2_root / "two.md").write_text("two", encoding="utf-8")
        self.service.harness_add("h1", session_root=str(self.h1_root), mcp_mounted=True)
        self.service.harness_add("h2", session_root=str(self.h2_root), mcp_mounted=True)
        self.service.source_approve(str(self.h1_root), harness_key="h1")
        self.service.source_approve(str(self.h2_root), harness_key="h2")
        self.service.harness_add("h1", harvest_ok=True)
        self.service.harness_add("h2", harvest_ok=True)
        self.h1_source = self.service.store.source_by_root(self.h1_root)
        self.h2_source = self.service.store.source_by_root(self.h2_root)
        assert self.h1_source is not None and self.h2_source is not None
        self.h1 = MemoryService(self.data_dir, principal="h1")
        self.h2 = MemoryService(self.data_dir, principal="h2")

    def tearDown(self) -> None:
        self.h1.close()
        self.h2.close()
        super().tearDown()

    def _submit(self, service: MemoryService, source_uid: str, submission_uid: str, text: str) -> dict:
        return service.candidate_add(
            content=text,
            submission_uid=submission_uid,
            entities=["With"],
            source_uid=source_uid,
            source_locator="one.md",
        )

    def test_candidate_idempotency_is_isolated_per_principal(self) -> None:
        submission = str(uuid.uuid4())
        first = self._submit(
            self.h1,
            self.h1_source["source_uid"],
            submission,
            "Harness one proposes a concise memory.",
        )
        retried = self._submit(
            self.h1,
            self.h1_source["source_uid"],
            submission,
            "Harness one proposes a concise memory.",
        )
        second_principal = self._submit(
            self.h2,
            self.h2_source["source_uid"],
            submission,
            "Harness two may reuse the same local idempotency key.",
        )
        self.assertEqual(first["candidate"]["candidate_uid"], retried["candidate"]["candidate_uid"])
        self.assertFalse(retried["created"])
        self.assertNotEqual(
            first["candidate"]["candidate_uid"],
            second_principal["candidate"]["candidate_uid"],
        )
        self.assertEqual(
            {item["principal_key"] for item in self.h1.candidate_list(status=None)["candidates"]},
            {"h1"},
        )

    def test_uuid_session_locator_is_preserved_without_false_secret_rejection(self) -> None:
        locator = "2026/09/05/rollout-2026-09-05T10-37-13-ba27de96-615a-4286-b04e-d94a57ca15d9.jsonl"
        candidate = self.h1.candidate_add(
            content="A durable preference from an approved session.",
            submission_uid="uuid-session",
            source_uid=self.h1_source["source_uid"],
            source_locator=locator,
        )["candidate"]
        self.assertEqual(candidate["status"], "pending")
        self.assertEqual(candidate["source_locator"], locator)

    def test_uuid_locator_does_not_bypass_explicit_or_unstructured_secrets(self) -> None:
        session = "ba27de96-615a-4286-b04e-d94a57ca15d9"
        for secret in ["sk-" + "ThisCredentialMustNeverPersist0123456789", "A7fK9mP2qR8sT4vW6xY1zB3cD5eG7hJ9"]:
            with self.subTest(secret_kind=secret[:3]), self.assertRaises(ContentRejectedError):
                self.h1.candidate_add(
                    content="A safe pointer.",
                    submission_uid="reject-secret-locator",
                    source_uid=self.h1_source["source_uid"],
                    source_locator=f"{session}/{secret}.jsonl",
                )
        self.assertEqual(self.h1.candidate_list()["candidates"], [])

    def test_source_assignment_is_enforced_for_submit_list_and_scan(self) -> None:
        with self.assertRaises(PermissionRequiredError):
            self._submit(
                self.h1,
                self.h2_source["source_uid"],
                str(uuid.uuid4()),
                "This candidate cites another harness source.",
            )
        with self.assertRaises(PermissionRequiredError):
            self.h1.harvest_begin(self.h2_source["source_uid"])
        self.assertEqual(
            {item["source_uid"] for item in self.h1.source_list()["sources"]},
            {self.h1_source["source_uid"]},
        )

    def test_auxiliary_submission_and_cursor_secrets_never_persist(self) -> None:
        secret = "sk-" + "AuxiliaryCredentialMustNeverPersist0123456789"
        with self.assertRaises(ContentRejectedError):
            self._submit(self.h1, self.h1_source["source_uid"], secret, "A safe pointer.")
        run = self.h1.harvest_begin(self.h1_source["source_uid"])
        with self.assertRaises(ContentRejectedError):
            self.h1.harvest_complete(run["run_uid"], cursor=secret)
        self.assertEqual(self.h1.store.scan_run(run["run_uid"])["status"], "open")
        for path in self.data_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(secret.encode(), path.read_bytes(), str(path))

    def test_explicit_scope_keys_cannot_persist_credentials(self) -> None:
        secret = "sk-" + "ScopeCredentialMustNeverPersist0123456789"
        for kind in ("project", "workspace"):
            with self.subTest(kind=kind):
                scope = {"scope": kind, kind: secret}
                with self.assertRaises(ContentRejectedError):
                    self.service.add("A safe pointer.", **scope)
                with self.assertRaises(ContentRejectedError):
                    self.service.candidate_add(
                        content="A safe candidate.", submission_uid="scope-check", manual_input=True, **scope
                    )
                with self.assertRaises(ContentRejectedError):
                    self.service.harness_grant("h1", FACT_SEARCH, scope=kind, scope_key=secret)
        for path in self.data_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(secret.encode(), path.read_bytes(), str(path))

    def test_review_accept_is_atomic_and_candidate_body_is_cleared(self) -> None:
        submitted = self._submit(
            self.h1,
            self.h1_source["source_uid"],
            str(uuid.uuid4()),
            "The reviewed pointer becomes an immutable active fact.",
        )["candidate"]
        old = self.service.add("An older active pointer will be superseded.")["fact"]
        accepted = self.service.review_accept(submitted["candidate_uid"], supersedes=[old["fact_id"]])
        self.assertEqual(accepted["candidate"]["status"], "accepted")
        self.assertIsNone(accepted["candidate"]["content"])
        self.assertEqual(accepted["fact"]["status"], "active")
        self.assertEqual(accepted["fact"]["source_count"], 1)
        self.assertEqual(self.service.store.get_fact(old["fact_id"]).status, "deprecated")

    def test_reject_and_expire_remove_original_candidate_body(self) -> None:
        rejected = self._submit(
            self.h1,
            self.h1_source["source_uid"],
            str(uuid.uuid4()),
            "This candidate will be rejected after review.",
        )["candidate"]
        result = self.service.review_reject(rejected["candidate_uid"], reason="not durable")
        self.assertEqual(result["candidate"]["status"], "rejected")
        self.assertIsNone(result["candidate"]["content"])

        expiring = self._submit(
            self.h1,
            self.h1_source["source_uid"],
            str(uuid.uuid4()),
            "This candidate will reach its retention boundary.",
        )["candidate"]
        with self.service.store.write_transaction():
            self.service.store.connection.execute(
                "UPDATE candidates SET expires_at = '2000-01-01T00:00:00Z' WHERE candidate_uid = ?",
                (expiring["candidate_uid"],),
            )
        self.service.candidate_list(status=None)
        expired = self.service.candidate_show(expiring["candidate_uid"])["candidate"]
        self.assertEqual(expired["status"], "expired")
        self.assertIsNone(expired["content"])

    def test_expired_candidates_cannot_be_read_or_accepted_without_listing(self) -> None:
        for operation in ("candidate_show", "review_context", "review_accept"):
            candidate = self._submit(self.h1, self.h1_source["source_uid"], str(uuid.uuid4()), "Temporary pointer.")[
                "candidate"
            ]
            uid = candidate["candidate_uid"]
            with self.service.store.write_transaction():
                self.service.store.connection.execute(
                    "UPDATE candidates SET expires_at = '2000-01-01T00:00:00Z' WHERE candidate_uid = ?", (uid,)
                )
            with self.subTest(operation=operation):
                if operation == "review_accept":
                    with self.assertRaises(ConflictError):
                        self.service.review_accept(uid)
                else:
                    result = getattr(self.service, operation)(uid)["candidate"]
                    self.assertEqual(result["status"], "expired")
                    self.assertIsNone(result["content"])
                self.assertIsNone(self.service.store.get_candidate(uid).content)
        self.assertEqual(self.service.store.counts()["facts"], 0)

    def test_expiry_during_acceptance_is_rechecked_and_persisted(self) -> None:
        candidate = self._submit(self.h1, self.h1_source["source_uid"], "expiry-race", "Temporary pointer.")[
            "candidate"
        ]
        uid = candidate["candidate_uid"]
        accept = self.service.store._accept_current_candidate

        def expire_then_accept(*args, **kwargs):
            with self.service.store.write_transaction():
                self.service.store.connection.execute(
                    "UPDATE candidates SET expires_at = '2000-01-01T00:00:00Z' WHERE candidate_uid = ?", (uid,)
                )
            return accept(*args, **kwargs)

        with (
            patch.object(self.service.store, "_accept_current_candidate", side_effect=expire_then_accept),
            self.assertRaises(ConflictError),
        ):
            self.service.review_accept(uid)
        self.assertEqual(self.service.store.get_candidate(uid).status, "expired")
        self.assertIsNone(self.service.store.get_candidate(uid).content)

    def test_revoked_source_clears_pending_candidates_and_prevents_acceptance(self) -> None:
        uid = self._submit(self.h1, self.h1_source["source_uid"], "revoke-pending", "Pending source pointer.")[
            "candidate"
        ]["candidate_uid"]
        self.service.source_revoke(self.h1_source["source_uid"])
        candidate = self.service.candidate_show(uid)["candidate"]
        self.assertEqual(candidate["status"], "rejected")
        self.assertIsNone(candidate["content"])
        with self.assertRaises(ConflictError):
            self.service.review_accept(uid)
        self.assertEqual(self.service.store.counts()["facts"], 0)

    def test_source_revocation_and_acceptance_are_serialized(self) -> None:
        uid = self._submit(self.h1, self.h1_source["source_uid"], "revoke-race", "Reviewed source pointer.")[
            "candidate"
        ]["candidate_uid"]
        other = MemoryService(self.data_dir)
        insert = self.service.store._insert_fact_no_tx

        def insert_with_competing_revoke(*args, **kwargs):
            with self.assertRaises(ConflictError):
                other.source_revoke(self.h1_source["source_uid"])
            return insert(*args, **kwargs)

        try:
            with patch.object(self.service.store, "_insert_fact_no_tx", side_effect=insert_with_competing_revoke):
                result = self.service.review_accept(uid)
            self.assertEqual(result["candidate"]["status"], "accepted")
            other.source_revoke(self.h1_source["source_uid"])
            self.assertEqual(self.service.store.get_fact(result["fact"]["fact_id"]).status, "active")
        finally:
            other.close()

    def test_unknown_scope_revoke_preserves_unscoped_grant(self) -> None:
        self.service.harness_grant("h1", FACT_WRITE)
        with self.assertRaises(NotFoundError):
            self.service.harness_revoke_grant("h1", FACT_WRITE, scope="project", scope_key="TypoDoesNotExist")
        self.assertEqual(self.h1.add("Global writer grant remains intact.")["fact"]["status"], "active")

    def test_forbidden_and_quarantined_content_has_zero_raw_persistence(self) -> None:
        rejected_secret = "sk-ThisCredentialMustNeverPersist0123456789"  # nosec-secret-test
        with self.assertRaises(ContentRejectedError):
            self.service.add(f"Credential: {rejected_secret}.")

        suspicious = "High entropy token: A7fK9mP2qR8sT4vW6xY1zB3cD5eG7hJ9."
        quarantined = self.service.candidate_add(
            content=suspicious,
            submission_uid=str(uuid.uuid4()),
            manual_input=True,
        )["candidate"]
        self.assertEqual(quarantined["status"], "quarantined")
        self.assertIsNone(quarantined["content"])

        minor = "学生小明的考试成绩是 91 分。"
        minor_candidate = self.service.candidate_add(
            content=minor,
            submission_uid=str(uuid.uuid4()),
            manual_input=True,
        )["candidate"]
        self.assertEqual(minor_candidate["status"], "quarantined")
        self.assertIsNone(minor_candidate["content"])

        needles = [rejected_secret.encode(), suspicious.encode(), minor.encode()]
        for path in self.data_dir.rglob("*"):
            if not path.is_file():
                continue
            payload = path.read_bytes()
            for needle in needles:
                self.assertNotIn(needle, payload, str(path))

    def test_source_revoke_invalidates_runs_and_index_but_keeps_facts(self) -> None:
        pending = self._submit(
            self.h1,
            self.h1_source["source_uid"],
            str(uuid.uuid4()),
            "A source-backed fact remains after consent is revoked.",
        )["candidate"]
        accepted = self.service.review_accept(pending["candidate_uid"])["fact"]
        run = self.h1.harvest_begin(self.h1_source["source_uid"])
        self.service.source_revoke(self.h1_source["source_uid"])
        self.assertEqual(self.service.store.scan_run(run["run_uid"])["status"], "revoked")
        self.assertEqual(self.service.store.list_files(), [])
        self.assertFalse(any(item["path"] == str(self.h1_root.resolve()) for item in self.service.store.list_folders()))
        self.assertIsNotNone(self.service.store.get_fact(accepted["fact_id"]))
        self.assertEqual(
            self.service.store.get_source(self.h1_source["source_uid"])["status"],
            "revoked",
        )

    def test_scope_acl_and_history_acl_never_cross_leak(self) -> None:
        user_active = self.service.add("ACL marker current user pointer.")["fact"]
        project_active = self.service.add("ACL marker current project pointer.", scope="project", project="With")[
            "fact"
        ]
        user_old = self.service.add("ACL history marker user pointer.")["fact"]
        project_old = self.service.add("ACL history marker project pointer.", scope="project", project="With")["fact"]
        self.service.deprecate(user_old["fact_id"])
        self.service.deprecate(project_old["fact_id"])

        default = self.h1.search("ACL marker", include_files=False, limit=20)
        self.assertIn(user_active["fact_id"], {item["fact_id"] for item in default["facts"]})
        self.assertNotIn(project_active["fact_id"], {item["fact_id"] for item in default["facts"]})
        with self.assertRaises(PermissionRequiredError):
            self.h1.search("ACL", scope="project", project="With", include_files=False)

        self.service.harness_grant("h1", FACT_SEARCH, scope="project", scope_key="With")
        self.service.harness_grant("h1", HISTORY_READ, scope="user", scope_key="")
        mixed = self.h1.search("ACL", include_deprecated=True, include_files=False, limit=100)["facts"]
        ids = {item["fact_id"] for item in mixed}
        self.assertIn(user_old["fact_id"], ids)
        self.assertNotIn(project_old["fact_id"], ids)
        self.assertIn(project_active["fact_id"], ids)

        self.service.harness_grant("h1", HISTORY_READ, scope="project", scope_key="With")
        project_history = self.h1.search(
            "ACL",
            include_deprecated=True,
            scope="project",
            project="With",
            include_files=False,
            limit=100,
        )["facts"]
        self.assertIn(project_old["fact_id"], {item["fact_id"] for item in project_history})

    def test_fact_id_operations_enforce_every_referenced_scope(self) -> None:
        alpha = self.service.add("Alpha original.", scope="project", project="Alpha")["fact"]
        beta = self.service.add("Beta protected.", scope="project", project="Beta")["fact"]
        for capability in (FACT_SEARCH, FACT_WRITE, HISTORY_READ):
            self.service.harness_grant("h1", capability, scope="project", scope_key="Alpha")
        with self.assertRaises(PermissionRequiredError):
            self.h1.history(beta["fact_id"])
        with self.assertRaises(PermissionRequiredError):
            self.h1.deprecate(beta["fact_id"])
        with self.assertRaises(PermissionRequiredError):
            self.h1.deprecate(alpha["fact_id"], superseded_by=beta["fact_id"])
        with self.assertRaises(PermissionRequiredError):
            self.h1.add("Unauthorized replacement.", scope="project", project="Alpha", supersedes=beta["fact_id"])
        self.assertEqual(self.service.store.get_fact(beta["fact_id"]).status, "active")
        self.assertEqual(self.service.store.get_fact(alpha["fact_id"]).status, "active")
        self.assertEqual(self.service.store.counts()["facts"], 2)
        self.service.deprecate(alpha["fact_id"], superseded_by=beta["fact_id"])
        with self.assertRaises(PermissionRequiredError):
            self.h1.history(alpha["fact_id"])
        self.service.harness_grant("h1", HISTORY_READ, scope="project", scope_key="Beta")
        with self.assertRaises(PermissionRequiredError):
            self.h1.history(alpha["fact_id"])
        self.service.harness_grant("h1", FACT_SEARCH, scope="project", scope_key="Beta")
        self.assertEqual(len(self.h1.history(alpha["fact_id"])["chain"]), 2)

    def test_legacy_status_is_redacted_and_legacy_search_still_works(self) -> None:
        self.service.add("Legacy principal may search this user pointer.", entities=["legacy-probe"])
        legacy = MemoryService(self.data_dir, principal="legacy")
        try:
            status = legacy.status()
            self.assertEqual(status["harnesses"], [])
            self.assertEqual(status["sources"], [])
            found = legacy.search("legacy-probe", include_files=False)
            self.assertEqual(len(found["facts"]), 1)
            with self.assertRaises(PermissionRequiredError):
                legacy.add("Legacy must not write an active fact.")
        finally:
            legacy.close()
