"""
Unit tests for car_heater_kfactor.py SQLAlchemy migration.

Tests cover:
- KFactor session recording and retrieval
- KFactor result recording and retrieval
- KFactor prediction outcome recording
- Active params singleton CRUD
- Bucket params CRUD with wind_bucket encoding
- Config singleton CRUD
- Cooldown singleton CRUD
- Calibration statistics
- Sessions with results JOIN query
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from app.core import Controller
from app.core.models import (
    CarHeaterKFactorActiveParams,
    CarHeaterKFactorBucketParams,
    CarHeaterKFactorConfig,
    CarHeaterKFactorCooldown,
    CarHeaterKFactorPredictionOutcome,
    CarHeaterKFactorResult,
    CarHeaterKFactorSession,
)
from app.core.schema import metadata


@pytest.fixture
def temp_db():
    """Create a temporary PostgreSQL-like database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield str(db_path)


@pytest.fixture
def controller(temp_db: str):
    """Create a controller with SQLAlchemy engine for testing."""
    ctrl = Controller(db_path=temp_db)

    # Create SQLAlchemy engine pointing to a temp PostgreSQL-like SQLite
    pg_db_path = temp_db.replace(".db", "_pg.db")
    engine = create_engine(f"sqlite:///{pg_db_path}", echo=False)

    # Create tables from schema
    metadata.create_all(engine)

    ctrl._sa_engine = engine
    yield ctrl
    engine.dispose()


def make_session(
    start_ts: datetime | str | None = None,
    end_ts: datetime | str | None = None,
    **kwargs,
) -> CarHeaterKFactorSession:
    """Helper to create CarHeaterKFactorSession with sensible defaults."""
    if start_ts is None:
        start_ts = datetime.now(UTC)
    if isinstance(start_ts, datetime):
        start_ts = start_ts.isoformat()
    if end_ts is None:
        end_ts = datetime.now(UTC) + timedelta(hours=1)
    if isinstance(end_ts, datetime):
        end_ts = end_ts.isoformat()

    return CarHeaterKFactorSession(
        id=kwargs.get("id"),
        start_ts=start_ts,
        end_ts=end_ts,
        auto_window_date=kwargs.get("auto_window_date"),
        auto_window_start=kwargs.get("auto_window_start"),
        auto_window_stop=kwargs.get("auto_window_stop"),
        heater_mode=kwargs.get("heater_mode", "active"),
        outside_t_mean=kwargs.get("outside_t_mean", -10.0),
        outside_t_min=kwargs.get("outside_t_min", -15.0),
        outside_t_max=kwargs.get("outside_t_max", -5.0),
        wind_mean=kwargs.get("wind_mean", 5.0),
        cabin_t_start=kwargs.get("cabin_t_start", -8.0),
        cabin_t_end=kwargs.get("cabin_t_end", 15.0),
        cabin_t_max=kwargs.get("cabin_t_max", 18.0),
        duration_s=kwargs.get("duration_s", 3600),
        sample_count=kwargs.get("sample_count", 60),
        flags_json=kwargs.get("flags_json"),
        quality_score=kwargs.get("quality_score", 0.85),
        accepted=kwargs.get("accepted", True),
    )


def make_result(
    session_id: int,
    created_ts: datetime | str | None = None,
    **kwargs,
) -> CarHeaterKFactorResult:
    """Helper to create CarHeaterKFactorResult with sensible defaults."""
    if created_ts is None:
        created_ts = datetime.now(UTC)
    if isinstance(created_ts, datetime):
        created_ts = created_ts.isoformat()

    return CarHeaterKFactorResult(
        id=kwargs.get("id"),
        session_id=session_id,
        model_version=kwargs.get("model_version", "v1.0"),
        k_loss_W_per_K=kwargs.get("k_loss_W_per_K", 30.0),
        eta=kwargs.get("eta", 0.85),
        rmse_C=kwargs.get("rmse_C", 0.5),
        r2=kwargs.get("r2", 0.95),
        confidence=kwargs.get("confidence", 0.9),
        promoted=kwargs.get("promoted", False),
        created_ts=created_ts,
    )


class TestKFactorSession:
    """Tests for kfactor session recording and retrieval."""

    def test_record_session_creates_new_entry(self, controller: Controller):
        """Test that recording a session creates a new entry with returned ID."""
        session = make_session(
            start_ts=datetime.now(UTC),
            end_ts=datetime.now(UTC) + timedelta(hours=1),
            heater_mode="active",
            outside_t_mean=-10.0,
            quality_score=0.9,
            accepted=True,
        )

        result = controller.record_kfactor_session(session)

        assert result.id is not None
        assert result.id > 0
        assert result.heater_mode == "active"
        assert result.quality_score == 0.9

    def test_record_multiple_sessions(self, controller: Controller):
        """Test recording multiple sessions creates sequential IDs."""
        ids = []
        for i in range(3):
            session = make_session(
                start_ts=datetime.now(UTC) + timedelta(hours=i),
                quality_score=0.8 + i * 0.05,
            )
            result = controller.record_kfactor_session(session)
            ids.append(result.id)

        assert ids[0] < ids[1] < ids[2]

    def test_get_recent_sessions(self, controller: Controller):
        """Test retrieving recent sessions."""
        for i in range(5):
            session = make_session(
                start_ts=datetime.now(UTC) + timedelta(hours=i),
                quality_score=0.8 + i * 0.02,
            )
            controller.record_kfactor_session(session)

        results = controller.get_recent_kfactor_sessions(limit=3)

        assert len(results) == 3
        # Should be in descending order by start_ts
        assert results[0].quality_score > results[1].quality_score

    def test_get_recent_sessions_accepted_only(self, controller: Controller):
        """Test retrieving only accepted sessions."""
        for i in range(4):
            session = make_session(
                start_ts=datetime.now(UTC) + timedelta(hours=i),
                accepted=(i % 2 == 0),  # Alternate accepted
            )
            controller.record_kfactor_session(session)

        results = controller.get_recent_kfactor_sessions(limit=10, accepted_only=True)

        assert all(r.accepted for r in results)
        assert len(results) == 2

    def test_record_session_retries_after_postgres_sequence_drift(self, controller: Controller):
        """Test stale PostgreSQL PK sequence is realigned and insert is retried once."""

        class _FakeDiag:
            constraint_name = "car_heater_kfactor_session_pkey"

        class _FakeOrig(Exception):
            sqlstate = "23505"
            diag = _FakeDiag()

        class _FakeResult:
            def __init__(self, value):
                self._value = value

            def scalar_one(self):
                return self._value

            def scalar_one_or_none(self):
                return self._value

        class _FakeConn:
            def __init__(self, state):
                self._state = state

            def execute(self, stmt, params=None):
                self._state.calls.append((stmt, params))
                call_index = len(self._state.calls)
                if call_index == 1:
                    raise IntegrityError("INSERT", {}, _FakeOrig())
                if call_index == 2:
                    return _FakeResult("public.car_heater_kfactor_session_id_seq")
                if call_index == 3:
                    return _FakeResult(11)
                if call_index == 4:
                    return _FakeResult(None)
                if call_index == 5:
                    return _FakeResult(12)
                raise AssertionError(f"Unexpected execute call {call_index}")

        class _FakeEngine:
            def __init__(self):
                self.dialect = SimpleNamespace(name="postgresql")
                self.calls = []

            @contextmanager
            def begin(self):
                yield _FakeConn(self)

        fake_engine = _FakeEngine()
        controller._sa_engine = fake_engine

        session = make_session()
        persisted = controller.record_kfactor_session(session)

        assert persisted.id == 12
        assert len(fake_engine.calls) == 5
        # Sequence lookup should target the same table name as the failed insert.
        assert fake_engine.calls[1][1] == {"table_name": "car_heater_kfactor_session"}


class TestKFactorResult:
    """Tests for kfactor result recording and retrieval."""

    def test_record_result_creates_new_entry(self, controller: Controller):
        """Test that recording a result creates a new entry with returned ID."""
        # First create a session
        session = make_session()
        saved_session = controller.record_kfactor_session(session)

        result = make_result(
            session_id=saved_session.id,
            k_loss_W_per_K=35.0,
            eta=0.88,
            promoted=True,
        )

        saved_result = controller.record_kfactor_result(result)

        assert saved_result.id is not None
        assert saved_result.id > 0
        assert saved_result.session_id == saved_session.id
        assert saved_result.k_loss_W_per_K == 35.0

    def test_get_recent_results(self, controller: Controller):
        """Test retrieving recent results."""
        session = make_session()
        saved_session = controller.record_kfactor_session(session)

        for i in range(5):
            result = make_result(
                session_id=saved_session.id,
                created_ts=datetime.now(UTC) + timedelta(minutes=i),
                k_loss_W_per_K=30.0 + i,
            )
            controller.record_kfactor_result(result)

        results = controller.get_recent_kfactor_results(limit=3)

        assert len(results) == 3
        # Should be in descending order by created_ts
        assert results[0].k_loss_W_per_K > results[1].k_loss_W_per_K

    def test_get_recent_results_promoted_only(self, controller: Controller):
        """Test retrieving only promoted results."""
        session = make_session()
        saved_session = controller.record_kfactor_session(session)

        for i in range(4):
            result = make_result(
                session_id=saved_session.id,
                created_ts=datetime.now(UTC) + timedelta(minutes=i),
                promoted=(i % 2 == 0),
            )
            controller.record_kfactor_result(result)

        results = controller.get_recent_kfactor_results(limit=10, promoted_only=True)

        assert all(r.promoted for r in results)
        assert len(results) == 2

    def test_get_result_for_session(self, controller: Controller):
        """Test retrieving result for a specific session."""
        session = make_session()
        saved_session = controller.record_kfactor_session(session)

        result = make_result(session_id=saved_session.id, k_loss_W_per_K=42.0)
        controller.record_kfactor_result(result)

        found = controller.get_kfactor_result_for_session(session_id=saved_session.id)

        assert found is not None
        assert found.session_id == saved_session.id
        assert found.k_loss_W_per_K == 42.0

    def test_get_result_for_session_not_found(self, controller: Controller):
        """Test returning None when no result exists for session."""
        result = controller.get_kfactor_result_for_session(session_id=9999)
        assert result is None


class TestKFactorPredictionOutcome:
    """Tests for prediction outcome recording."""

    def test_record_prediction_outcome(self, controller: Controller):
        """Test recording a prediction outcome."""
        outcome = CarHeaterKFactorPredictionOutcome(
            id=None,
            predicted_minutes=45.0,
            actual_minutes=50.0,
            error_minutes=5.0,
            cabin_start_c=-5.0,
            cabin_end_c=18.0,
            target_c=20.0,
            outside_c=-10.0,
            created_ts=datetime.now(UTC).isoformat(),
        )

        # Should not raise
        controller.record_kfactor_prediction_outcome(outcome)


class TestKFactorActiveParams:
    """Tests for active params singleton CRUD."""

    def test_get_active_params_empty(self, controller: Controller):
        """Test getting active params when none exist."""
        result = controller.get_kfactor_active_params()
        assert result is None

    def test_save_and_get_active_params(self, controller: Controller):
        """Test saving and retrieving active params."""
        params = CarHeaterKFactorActiveParams(
            id=1,
            k_loss_W_per_K=32.0,
            eta=0.87,
            updated_ts=datetime.now(UTC).isoformat(),
            source="test",
        )

        controller.save_kfactor_active_params(params)
        result = controller.get_kfactor_active_params()

        assert result is not None
        assert result.k_loss_W_per_K == 32.0
        assert result.eta == 0.87
        assert result.source == "test"

    def test_update_active_params(self, controller: Controller):
        """Test updating existing active params."""
        params1 = CarHeaterKFactorActiveParams(
            id=1,
            k_loss_W_per_K=30.0,
            eta=0.85,
            updated_ts=datetime.now(UTC).isoformat(),
            source="initial",
        )
        controller.save_kfactor_active_params(params1)

        params2 = CarHeaterKFactorActiveParams(
            id=1,
            k_loss_W_per_K=35.0,
            eta=0.90,
            updated_ts=datetime.now(UTC).isoformat(),
            source="updated",
        )
        controller.save_kfactor_active_params(params2)

        result = controller.get_kfactor_active_params()
        assert result.k_loss_W_per_K == 35.0
        assert result.source == "updated"


class TestKFactorBucketParams:
    """Tests for bucket params CRUD with wind encoding."""

    def test_save_and_get_bucket_params(self, controller: Controller):
        """Test saving and retrieving bucket params."""
        params = CarHeaterKFactorBucketParams(
            id=None,
            t_bucket=-10,
            wind_bucket=2,
            k_loss_W_per_K=28.0,
            eta=0.82,
            updated_ts=datetime.now(UTC).isoformat(),
            source="test",
        )

        controller.save_kfactor_bucket_params(params)
        result = controller.get_kfactor_bucket_params(t_bucket=-10, wind_bucket=2)

        assert result is not None
        assert result.t_bucket == -10
        assert result.wind_bucket == 2
        assert result.k_loss_W_per_K == 28.0

    def test_bucket_params_with_none_wind(self, controller: Controller):
        """Test bucket params with wind_bucket=None (encoded as -999)."""
        params = CarHeaterKFactorBucketParams(
            id=None,
            t_bucket=-5,
            wind_bucket=None,  # Unknown wind
            k_loss_W_per_K=25.0,
            eta=0.80,
            updated_ts=datetime.now(UTC).isoformat(),
            source="test",
        )

        controller.save_kfactor_bucket_params(params)
        result = controller.get_kfactor_bucket_params(t_bucket=-5, wind_bucket=None)

        assert result is not None
        assert result.wind_bucket is None

    def test_get_bucket_params_any_wind(self, controller: Controller):
        """Test retrieving bucket params for any wind bucket."""
        # Save with specific wind bucket
        params = CarHeaterKFactorBucketParams(
            id=None,
            t_bucket=-15,
            wind_bucket=3,
            k_loss_W_per_K=33.0,
            eta=0.88,
            updated_ts=datetime.now(UTC).isoformat(),
            source="test",
        )
        controller.save_kfactor_bucket_params(params)

        # Get with any wind
        result = controller.get_kfactor_bucket_params_any_wind(t_bucket=-15)

        assert result is not None
        assert result.t_bucket == -15
        assert result.k_loss_W_per_K == 33.0

    def test_get_all_bucket_params(self, controller: Controller):
        """Test retrieving all bucket params."""
        for t in [-20, -10, 0]:
            for w in [1, 2]:
                params = CarHeaterKFactorBucketParams(
                    id=None,
                    t_bucket=t,
                    wind_bucket=w,
                    k_loss_W_per_K=30.0 + t,
                    eta=0.85,
                    updated_ts=datetime.now(UTC).isoformat(),
                    source="test",
                )
                controller.save_kfactor_bucket_params(params)

        results = controller.get_all_bucket_params()

        assert len(results) == 6

    def test_upsert_bucket_params(self, controller: Controller):
        """Test that saving same bucket updates rather than duplicates."""
        params1 = CarHeaterKFactorBucketParams(
            id=None,
            t_bucket=-10,
            wind_bucket=2,
            k_loss_W_per_K=28.0,
            eta=0.82,
            updated_ts=datetime.now(UTC).isoformat(),
            source="initial",
        )
        controller.save_kfactor_bucket_params(params1)

        params2 = CarHeaterKFactorBucketParams(
            id=None,
            t_bucket=-10,
            wind_bucket=2,
            k_loss_W_per_K=35.0,
            eta=0.90,
            updated_ts=datetime.now(UTC).isoformat(),
            source="updated",
        )
        controller.save_kfactor_bucket_params(params2)

        all_params = controller.get_all_bucket_params()
        assert len(all_params) == 1
        assert all_params[0].k_loss_W_per_K == 35.0


class TestKFactorConfig:
    """Tests for config singleton CRUD."""

    def test_get_config_empty(self, controller: Controller):
        """Test getting config when none exists."""
        result = controller.get_kfactor_config()
        assert result is None

    def test_save_and_get_config(self, controller: Controller):
        """Test saving and retrieving config."""
        config = CarHeaterKFactorConfig(
            id=1,
            config_json='{"enabled": true, "threshold": 0.8}',
            updated_ts=datetime.now(UTC).isoformat(),
        )

        controller.save_kfactor_config(config)
        result = controller.get_kfactor_config()

        assert result is not None
        assert "enabled" in result.config_json

    def test_update_config(self, controller: Controller):
        """Test updating existing config."""
        config1 = CarHeaterKFactorConfig(
            id=1,
            config_json='{"enabled": false}',
            updated_ts=datetime.now(UTC).isoformat(),
        )
        controller.save_kfactor_config(config1)

        config2 = CarHeaterKFactorConfig(
            id=1,
            config_json='{"enabled": true, "new_field": 123}',
            updated_ts=datetime.now(UTC).isoformat(),
        )
        controller.save_kfactor_config(config2)

        result = controller.get_kfactor_config()
        assert "new_field" in result.config_json


class TestKFactorCooldown:
    """Tests for cooldown singleton CRUD."""

    def test_get_cooldown_empty(self, controller: Controller):
        """Test getting cooldown when none exists."""
        result = controller.get_kfactor_cooldown()
        assert result is None

    def test_save_and_get_cooldown(self, controller: Controller):
        """Test saving and retrieving cooldown."""
        cooldown = CarHeaterKFactorCooldown(
            id=1,
            cooldown_until=(datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            updated_ts=datetime.now(UTC).isoformat(),
        )

        controller.save_kfactor_cooldown(cooldown)
        result = controller.get_kfactor_cooldown()

        assert result is not None
        assert result.cooldown_until is not None

    def test_update_cooldown(self, controller: Controller):
        """Test updating existing cooldown."""
        cooldown1 = CarHeaterKFactorCooldown(
            id=1,
            cooldown_until=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            updated_ts=datetime.now(UTC).isoformat(),
        )
        controller.save_kfactor_cooldown(cooldown1)

        new_until = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
        cooldown2 = CarHeaterKFactorCooldown(
            id=1,
            cooldown_until=new_until,
            updated_ts=datetime.now(UTC).isoformat(),
        )
        controller.save_kfactor_cooldown(cooldown2)

        result = controller.get_kfactor_cooldown()
        assert result.cooldown_until == new_until


class TestCalibrationStats:
    """Tests for calibration statistics."""

    def test_empty_stats(self, controller: Controller):
        """Test stats when no data exists."""
        stats = controller.get_calibration_stats()

        assert stats["total_sessions"] == 0
        assert stats["accepted_sessions"] == 0
        assert stats["buckets_covered"] == 0

    def test_stats_with_data(self, controller: Controller):
        """Test stats with some data."""
        # Create sessions
        for i in range(5):
            session = make_session(
                start_ts=datetime.now(UTC) - timedelta(days=i),
                accepted=(i < 3),  # 3 accepted
                quality_score=0.8 + i * 0.02,
            )
            controller.record_kfactor_session(session)

        # Create bucket params
        for t in [-10, -5, 0]:
            params = CarHeaterKFactorBucketParams(
                id=None,
                t_bucket=t,
                wind_bucket=2,
                k_loss_W_per_K=30.0,
                eta=0.85,
                updated_ts=datetime.now(UTC).isoformat(),
                source="test",
            )
            controller.save_kfactor_bucket_params(params)

        stats = controller.get_calibration_stats()

        assert stats["total_sessions"] == 5
        assert stats["accepted_sessions"] == 3
        assert stats["buckets_covered"] == 3


class TestSessionsWithResults:
    """Tests for sessions joined with results."""

    def test_empty_results(self, controller: Controller):
        """Test when no sessions exist."""
        results = controller.get_sessions_with_results(limit=10)
        assert results == []

    def test_sessions_with_results(self, controller: Controller):
        """Test retrieving sessions with their fit results."""
        # Create session
        session = make_session(
            heater_mode="active",
            quality_score=0.9,
        )
        saved_session = controller.record_kfactor_session(session)

        # Create promoted result for it
        result = make_result(
            session_id=saved_session.id,
            k_loss_W_per_K=35.0,
            eta=0.88,
            promoted=True,
        )
        controller.record_kfactor_result(result)

        results = controller.get_sessions_with_results(limit=10)

        assert len(results) == 1
        assert results[0]["id"] == saved_session.id
        assert results[0]["mode"] == "active"
        assert results[0]["fit"] is not None
        assert results[0]["fit"]["k_loss"] == 35.0

    def test_sessions_without_promoted_result(self, controller: Controller):
        """Test sessions without promoted results show null fit."""
        session = make_session()
        saved_session = controller.record_kfactor_session(session)

        # Create non-promoted result
        result = make_result(session_id=saved_session.id, promoted=False)
        controller.record_kfactor_result(result)

        results = controller.get_sessions_with_results(limit=10)

        assert len(results) == 1
        assert results[0]["fit"] is None
