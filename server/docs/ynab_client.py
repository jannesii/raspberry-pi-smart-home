from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class YNABClient:
    def __init__(self, api_key: str, budget_id: str) -> None:
        self.base_url = "https://api.youneedabudget.com/v1"
        self.budget_id = budget_id
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def get_payees(self) -> dict[str, str]:
        logger.info("Fetching payees...")
        url = f"{self.base_url}/budgets/{self.budget_id}/payees"
        r = requests.get(url, headers=self.headers)
        if r.status_code == 200:
            data = {p["id"]: p["name"] for p in r.json()["data"]["payees"]}
            logger.info(f"Fetched payees count: {len(data)}")
            return data
        logger.exception(f"Failed to fetch payees: {r.status_code} - {r.text}")
        return {}

    def get_transactions(self) -> list[dict[str, Any]]:
        logger.info("Fetching transactions...")
        url = f"{self.base_url}/budgets/{self.budget_id}/transactions"
        r = requests.get(url, headers=self.headers)
        if r.status_code == 200:
            tx = r.json()["data"]["transactions"]
            logger.info(f"Fetched transactions count: {len(tx)}")
            return tx
        logger.exception(f"Failed to fetch transactions: {r.status_code} - {r.text}")
        return []

    @staticmethod
    def build_payee_to_category_map(
        transactions: list[dict[str, Any]], payees: dict[str, str]
    ) -> dict[str, str]:
        logger.info("Building payee to category mapping...")
        mapping: dict[str, str] = {}
        for t in transactions:
            payee_id = t.get("payee_id")
            category_id = t.get("category_id")
            if payee_id and category_id and payee_id in payees:
                mapping[payees[payee_id]] = category_id
        return mapping

    def bulk_upload(self, transactions: list[dict[str, Any]]) -> bool:
        if not transactions:
            logger.info("No transactions to upload.")
            return True
        logger.info(f"Uploading transactions (bulk)... count={len(transactions)}")
        url = f"{self.base_url}/budgets/{self.budget_id}/transactions/bulk"
        try:
            r = requests.post(url, headers=self.headers, json={"transactions": transactions})
            if r.status_code == 201:
                logger.info("Bulk transaction upload successful!")
                return True
            snippet = r.text[:500] if isinstance(r.text, str) else str(r.text)
            logger.error(f"Error in bulk transaction upload: {r.status_code} - {snippet}")
            return False
        except Exception as e:
            logger.exception(e)
            return False
