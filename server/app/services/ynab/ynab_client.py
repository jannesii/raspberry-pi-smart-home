from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class YNABClientError(RuntimeError):
    """Raised when YNAB API interaction fails."""


class YNABClient:
    def __init__(
        self,
        api_key: str,
        budget_id: str,
        *,
        timeout_s: int = 15,
        retries: int = 2,
        base_url: str = "https://api.youneedabudget.com/v1",
    ) -> None:
        logger.debug(
            "YNABClient.__init__ called budget_id=%s timeout_s=%s retries=%s",
            budget_id,
            timeout_s,
            retries,
        )
        self.base_url = base_url.rstrip("/")
        self.budget_id = budget_id
        self.timeout_s = int(timeout_s)
        self.retries = int(retries)
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

        retry = Retry(
            total=max(0, self.retries),
            read=max(0, self.retries),
            connect=max(0, self.retries),
            backoff_factor=0.4,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "POST", "PATCH", "PUT", "DELETE"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        logger.debug(
            "YNABClient._request method=%s path=%s params=%s payload_keys=%s",
            method,
            path,
            sorted(params.keys()) if isinstance(params, dict) else None,
            sorted(json_payload.keys()) if isinstance(json_payload, dict) else None,
        )
        try:
            response = self._session.request(
                method=method.upper(),
                url=url,
                params=params,
                json=json_payload,
                timeout=self.timeout_s,
            )
        except requests.RequestException as exc:
            logger.exception("YNAB request failed method=%s path=%s error=%s", method, path, exc)
            raise YNABClientError(f"YNAB request failed for {path}") from exc

        if response.status_code >= 400:
            body_snippet = response.text[:400] if isinstance(response.text, str) else ""
            logger.warning(
                "YNAB API error method=%s path=%s status=%s body=%s",
                method,
                path,
                response.status_code,
                body_snippet,
            )
            raise YNABClientError(f"YNAB API error for {path}: status={response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            logger.exception("YNAB invalid JSON method=%s path=%s", method, path)
            raise YNABClientError(f"YNAB response was not JSON for {path}") from exc

        if (
            not isinstance(payload, dict)
            or "data" not in payload
            or not isinstance(payload["data"], dict)
        ):
            logger.warning(
                "YNAB unexpected payload shape method=%s path=%s keys=%s",
                method,
                path,
                sorted(payload.keys()) if isinstance(payload, dict) else None,
            )
            raise YNABClientError(f"YNAB response missing data envelope for {path}")

        return payload["data"]

    def get_transactions_since(self, since_date: str | None = None) -> list[dict[str, Any]]:
        logger.debug("get_transactions_since called since_date=%s", since_date)
        params: dict[str, Any] = {}
        if since_date:
            params["since_date"] = since_date
        data = self._request(
            "GET",
            f"/budgets/{self.budget_id}/transactions",
            params=params or None,
        )
        transactions = data.get("transactions")
        if not isinstance(transactions, list):
            raise YNABClientError("YNAB transactions payload malformed")
        logger.debug("get_transactions_since fetched_count=%s", len(transactions))
        return transactions

    def get_categories(self) -> list[dict[str, Any]]:
        logger.debug("get_categories called")
        data = self._request("GET", f"/budgets/{self.budget_id}/categories")
        groups = data.get("category_groups")
        if not isinstance(groups, list):
            raise YNABClientError("YNAB categories payload malformed")
        logger.debug("get_categories fetched_group_count=%s", len(groups))
        return groups

    def update_transactions_bulk(self, items: list[dict[str, str]]) -> dict[str, Any]:
        logger.debug("update_transactions_bulk called count=%s", len(items))
        if not items:
            return {"transaction_ids": []}
        data = self._request(
            "PATCH",
            f"/budgets/{self.budget_id}/transactions",
            json_payload={"transactions": items},
        )
        tx_ids = data.get("transaction_ids")
        logger.debug(
            "update_transactions_bulk completed requested=%s updated=%s",
            len(items),
            len(tx_ids) if isinstance(tx_ids, list) else None,
        )
        return data
