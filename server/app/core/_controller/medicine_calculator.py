from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, delete, func, insert, select, update

from ..medicine_calculator import (
    calculate_medicine_refill,
    normalize_dosing_weekdays,
    normalize_medicine_name,
)
from ..models import MedicinePurchase, MedicineRefillCalculation
from ..schema import medicine_purchases

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class MedicineCalculatorMixin:
    def _medicine_require_sa_engine(self) -> Engine:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")
        return sa_engine

    def _medicine_now_iso(self) -> str:
        return datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]

    def _medicine_purchase_from_row(self, row: Any) -> MedicinePurchase:
        purchase = MedicinePurchase(
            id=int(row["id"]),
            medicine_name=str(row["medicine_name"]),
            medicine_key=str(row["medicine_key"]),
            purchase_date=str(row["purchase_date"]),
            pieces_bought=int(row["pieces_bought"]),
            dose_per_dosing_day=int(row["dose_per_dosing_day"]),
            dosing_weekdays_json=str(row["dosing_weekdays_json"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
        logger.debug("_medicine_purchase_from_row mapped purchase=%s", purchase)
        return purchase

    def _medicine_validate_payload(
        self,
        *,
        medicine_name: str,
        purchase_date: str,
        pieces_bought: int,
        dose_per_dosing_day: int,
        dosing_weekdays: list[int],
    ) -> tuple[str, str, str, int, int, list[int]]:
        logger.debug(
            (
                "_medicine_validate_payload called medicine_name=%s purchase_date=%s "
                "pieces_bought=%s dose_per_dosing_day=%s dosing_weekdays=%s"
            ),
            medicine_name,
            purchase_date,
            pieces_bought,
            dose_per_dosing_day,
            dosing_weekdays,
        )
        display_name = str(medicine_name or "").strip()
        medicine_key = normalize_medicine_name(display_name)
        parsed_purchase_date = datetime.strptime(str(purchase_date), "%Y-%m-%d").date().isoformat()
        pieces = int(pieces_bought)
        dose = int(dose_per_dosing_day)
        weekdays = normalize_dosing_weekdays(dosing_weekdays)

        calculate_medicine_refill(
            purchase_date=parsed_purchase_date,
            pieces_bought=pieces,
            dose_per_dosing_day=dose,
            dosing_weekdays=weekdays,
        )
        logger.debug(
            "_medicine_validate_payload normalized key=%s date=%s weekdays=%s",
            medicine_key,
            parsed_purchase_date,
            weekdays,
        )
        return display_name, medicine_key, parsed_purchase_date, pieces, dose, weekdays

    def list_medicine_purchases(self) -> list[MedicinePurchase]:
        logger.debug("list_medicine_purchases called")
        sa_engine = self._medicine_require_sa_engine()
        stmt = select(medicine_purchases).order_by(
            medicine_purchases.c.medicine_key.asc(),
            medicine_purchases.c.purchase_date.desc(),
            medicine_purchases.c.id.desc(),
        )
        with sa_engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        purchases = [self._medicine_purchase_from_row(row) for row in rows]
        logger.debug("list_medicine_purchases returning count=%s", len(purchases))
        return purchases

    def list_latest_medicine_purchases(self) -> list[MedicinePurchase]:
        """Return the latest purchase snapshot for each normalized medicine."""
        logger.debug("list_latest_medicine_purchases called")
        sa_engine = self._medicine_require_sa_engine()
        ranked_purchases = select(
            *medicine_purchases.c,
            func.row_number()
            .over(
                partition_by=medicine_purchases.c.medicine_key,
                order_by=(
                    medicine_purchases.c.purchase_date.desc(),
                    medicine_purchases.c.id.desc(),
                ),
            )
            .label("medicine_purchase_rank"),
        ).subquery()
        stmt = (
            select(*(ranked_purchases.c[column.name] for column in medicine_purchases.c))
            .where(ranked_purchases.c.medicine_purchase_rank == 1)
            .order_by(ranked_purchases.c.medicine_key.asc())
        )
        with sa_engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        purchases = [self._medicine_purchase_from_row(row) for row in rows]
        logger.debug("list_latest_medicine_purchases returning count=%s", len(purchases))
        return purchases

    def get_medicine_purchase(self, purchase_id: int) -> MedicinePurchase | None:
        logger.debug("get_medicine_purchase called purchase_id=%s", purchase_id)
        sa_engine = self._medicine_require_sa_engine()
        stmt = select(medicine_purchases).where(medicine_purchases.c.id == int(purchase_id))
        with sa_engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        if row is None:
            logger.debug("get_medicine_purchase no row for purchase_id=%s", purchase_id)
            return None
        return self._medicine_purchase_from_row(row)

    def create_medicine_purchase(
        self,
        *,
        medicine_name: str,
        purchase_date: str,
        pieces_bought: int,
        dose_per_dosing_day: int,
        dosing_weekdays: list[int],
    ) -> MedicinePurchase:
        logger.debug(
            "create_medicine_purchase called medicine_name=%s purchase_date=%s",
            medicine_name,
            purchase_date,
        )
        (
            display_name,
            medicine_key,
            parsed_purchase_date,
            pieces,
            dose,
            weekdays,
        ) = self._medicine_validate_payload(
            medicine_name=medicine_name,
            purchase_date=purchase_date,
            pieces_bought=pieces_bought,
            dose_per_dosing_day=dose_per_dosing_day,
            dosing_weekdays=dosing_weekdays,
        )
        now = self._medicine_now_iso()
        sa_engine = self._medicine_require_sa_engine()
        stmt = insert(medicine_purchases).values(
            medicine_name=display_name,
            medicine_key=medicine_key,
            purchase_date=parsed_purchase_date,
            pieces_bought=pieces,
            dose_per_dosing_day=dose,
            dosing_weekdays_json=json.dumps(weekdays),
            created_at=now,
            updated_at=now,
        )
        with sa_engine.begin() as conn:
            result = conn.execute(stmt)
            purchase_id = int(result.inserted_primary_key[0])
        created = self.get_medicine_purchase(purchase_id)
        if created is None:
            raise RuntimeError("Created medicine purchase could not be loaded")
        logger.debug("create_medicine_purchase created purchase=%s", created)
        return created

    def update_medicine_purchase(
        self,
        purchase_id: int,
        *,
        medicine_name: str,
        purchase_date: str,
        pieces_bought: int,
        dose_per_dosing_day: int,
        dosing_weekdays: list[int],
    ) -> MedicinePurchase:
        logger.debug("update_medicine_purchase called purchase_id=%s", purchase_id)
        existing = self.get_medicine_purchase(purchase_id)
        if existing is None:
            raise KeyError(f"Medicine purchase {purchase_id} not found")

        (
            display_name,
            medicine_key,
            parsed_purchase_date,
            pieces,
            dose,
            weekdays,
        ) = self._medicine_validate_payload(
            medicine_name=medicine_name,
            purchase_date=purchase_date,
            pieces_bought=pieces_bought,
            dose_per_dosing_day=dose_per_dosing_day,
            dosing_weekdays=dosing_weekdays,
        )
        sa_engine = self._medicine_require_sa_engine()
        stmt = (
            update(medicine_purchases)
            .where(medicine_purchases.c.id == int(purchase_id))
            .values(
                medicine_name=display_name,
                medicine_key=medicine_key,
                purchase_date=parsed_purchase_date,
                pieces_bought=pieces,
                dose_per_dosing_day=dose,
                dosing_weekdays_json=json.dumps(weekdays),
                updated_at=self._medicine_now_iso(),
            )
        )
        with sa_engine.begin() as conn:
            conn.execute(stmt)
        updated_purchase = self.get_medicine_purchase(purchase_id)
        if updated_purchase is None:
            raise RuntimeError("Updated medicine purchase could not be loaded")
        logger.debug("update_medicine_purchase updated purchase=%s", updated_purchase)
        return updated_purchase

    def delete_medicine_purchase(self, purchase_id: int) -> bool:
        logger.debug("delete_medicine_purchase called purchase_id=%s", purchase_id)
        sa_engine = self._medicine_require_sa_engine()
        stmt = delete(medicine_purchases).where(medicine_purchases.c.id == int(purchase_id))
        with sa_engine.begin() as conn:
            result = conn.execute(stmt)
        deleted = bool(result.rowcount)
        logger.debug("delete_medicine_purchase deleted=%s purchase_id=%s", deleted, purchase_id)
        return deleted

    def calculate_medicine_purchase(self, purchase: MedicinePurchase) -> MedicineRefillCalculation:
        logger.debug("calculate_medicine_purchase called purchase_id=%s", purchase.id)
        weekdays = json.loads(purchase.dosing_weekdays_json or "[]")
        result = calculate_medicine_refill(
            purchase_date=purchase.purchase_date,
            pieces_bought=purchase.pieces_bought,
            dose_per_dosing_day=purchase.dose_per_dosing_day,
            dosing_weekdays=weekdays,
        )
        logger.debug("calculate_medicine_purchase result=%s", result)
        return result

    def get_medicine_names(self) -> list[dict[str, str]]:
        logger.debug("get_medicine_names called")
        names_by_key: dict[str, str] = {}
        for purchase in self.list_medicine_purchases():
            names_by_key.setdefault(purchase.medicine_key, purchase.medicine_name)
        names = [
            {"medicine_key": key, "medicine_name": name}
            for key, name in sorted(names_by_key.items(), key=lambda item: item[1].casefold())
        ]
        logger.debug("get_medicine_names returning count=%s", len(names))
        return names

    def get_medicine_summaries(self) -> list[dict[str, Any]]:
        logger.debug("get_medicine_summaries called")
        latest_by_key: dict[str, MedicinePurchase] = {}
        for purchase in self.list_medicine_purchases():
            latest_by_key.setdefault(purchase.medicine_key, purchase)

        summaries: list[dict[str, Any]] = []
        for purchase in sorted(
            latest_by_key.values(), key=lambda item: item.medicine_name.casefold()
        ):
            summaries.append(
                {
                    "medicine_name": purchase.medicine_name,
                    "medicine_key": purchase.medicine_key,
                    "latest_purchase": asdict(purchase),
                    "calculation": asdict(self.calculate_medicine_purchase(purchase)),
                }
            )
        logger.debug("get_medicine_summaries returning count=%s", len(summaries))
        return summaries
