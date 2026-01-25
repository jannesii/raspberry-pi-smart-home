"""
KFactor thermal physics simulation and parameter fitting.

Contains the mathematical core for thermal model simulation and parameter estimation.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from statistics import mean
from typing import TYPE_CHECKING

from .constants import (
    HEAT_CAPACITY_J_PER_K,
    HEATER_POWER_W,
    clamp,
    linspace,
)
from .models import KFactorConfig, KFactorFit, KFactorSample

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ThermalPhysics:
    """Thermal model simulation and parameter fitting."""

    def __init__(self, cfg: KFactorConfig) -> None:
        self._cfg = cfg

    def simulate(
        self,
        times: list[datetime],
        T0: float,
        powers: list[float],
        *,
        tout: list[float],
        k_loss: float,
        eta: float,
    ) -> list[float] | None:
        """Simulate cabin temperature trajectory given parameters."""
        if k_loss <= 0.0 or not math.isfinite(k_loss):
            return None
        if not (self._cfg.eta_min <= eta <= self._cfg.eta_max) or not math.isfinite(eta):
            return None
        if len(tout) != len(times):
            return None

        T = float(T0)
        out: list[float] = [T]
        C_eff = HEAT_CAPACITY_J_PER_K * clamp(
            float(self._cfg.mass_factor),
            float(self._cfg.mass_factor_min),
            float(self._cfg.mass_factor_max),
        )

        for i in range(1, len(times)):
            dt_s = (times[i] - times[i - 1]).total_seconds()
            if dt_s <= 0:
                out.append(T)
                continue

            P = powers[i - 1] if i - 1 < len(powers) else powers[-1]
            P = float(P) if (P is not None and math.isfinite(P)
                             ) else HEATER_POWER_W
            P = P if P > 1.0 else HEATER_POWER_W

            Tout_i = float(tout[i - 1])
            Tss = Tout_i + (eta * P) / k_loss
            decay = math.exp(-(k_loss / C_eff) * dt_s)
            T = Tss + (T - Tss) * decay
            out.append(T)
        return out

    def rmse_for(
        self,
        times: list[datetime],
        obs: list[float],
        powers: list[float],
        *,
        tout: list[float],
        k_loss: float,
        eta: float,
    ) -> float:
        """Compute RMSE between simulated and observed temperatures."""
        pred = self.simulate(
            times, obs[0], powers, tout=tout, k_loss=k_loss, eta=eta)
        if pred is None:
            return float("inf")
        err2 = 0.0
        n = 0
        for y_hat, y in zip(pred, obs):
            if not (math.isfinite(y_hat) and math.isfinite(y)):
                continue
            d = y_hat - y
            err2 += d * d
            n += 1
        return math.sqrt(err2 / n) if n else float("inf")

    def r2_for(
        self,
        times: list[datetime],
        obs: list[float],
        powers: list[float],
        *,
        tout: list[float],
        k_loss: float,
        eta: float,
    ) -> float | None:
        """Compute R² coefficient of determination."""
        pred = self.simulate(
            times, obs[0], powers, tout=tout, k_loss=k_loss, eta=eta)
        if pred is None:
            return None
        y_bar = mean(obs)
        sse = 0.0
        sst = 0.0
        for y_hat, y in zip(pred, obs):
            if not (math.isfinite(y_hat) and math.isfinite(y)):
                continue
            d = y - y_hat
            sse += d * d
            v = y - y_bar
            sst += v * v
        if sst <= 0.0:
            return None
        return 1.0 - (sse / sst)

    def fit_params(
        self,
        samples: list[KFactorSample],
        current_k: float,
        current_eta: float,
    ) -> KFactorFit | None:
        """Fit k_loss and eta parameters from calibration samples."""
        if len(samples) < 3:
            return None

        # Build time/power series (ignore heater-off samples)
        series: list[tuple[datetime, float, float, float]] = []
        for s in samples:
            if not s.heater_on:
                continue
            series.append(
                (s.ts, float(s.cabin_temp_c), float(
                    s.power_w), float(s.outside_temp_c))
            )

        if len(series) < 3:
            return None

        times = [t for (t, _, _, _) in series]
        obs = [v for (_, v, _, _) in series]
        powers = [p for (_, _, p, _) in series]
        tout = [x for (_, _, _, x) in series]
        tout = self._smooth_series(tout, window=max(
            1, int(self._cfg.tout_smooth_window)))

        k0 = clamp(float(current_k), self._cfg.k_loss_min,
                   self._cfg.k_loss_max)
        eta0 = clamp(float(current_eta), self._cfg.eta_min, self._cfg.eta_max)

        # Two-step fitting to reduce k_loss/eta tradeoff:
        # 1) Fit k_loss with eta fixed
        # 2) Fit eta with k_loss fixed
        best_k, _best_rmse_k = self._fit_k_loss_only(
            times=times, obs=obs, powers=powers, tout=tout,
            k0=k0, eta_fixed=eta0,
        )
        best_eta, _best_rmse = self._fit_eta_only(
            times=times, obs=obs, powers=powers, tout=tout,
            eta0=eta0, k_fixed=best_k,
        )

        # Optional: small refinement pass around the two-step solution
        best_k, best_eta, best_rmse = self._refine_joint(
            times=times, obs=obs, powers=powers, tout=tout,
            k0=k0, eta0=eta0, k_start=best_k, eta_start=best_eta,
        )

        r2 = self.r2_for(times, obs, powers, tout=tout,
                         k_loss=best_k, eta=best_eta)
        return KFactorFit(
            k_loss_W_per_K=float(best_k),
            eta=float(best_eta),
            rmse_C=float(best_rmse),
            r2=r2,
        )

    def _fit_k_loss_only(
        self,
        *,
        times: list[datetime],
        obs: list[float],
        powers: list[float],
        tout: list[float],
        k0: float,
        eta_fixed: float,
    ) -> tuple[float, float]:
        """Fit k_loss with eta held fixed."""
        best_k = clamp(float(k0), self._cfg.k_loss_min, self._cfg.k_loss_max)
        best_rmse = self.rmse_for(
            times, obs, powers, tout=tout, k_loss=best_k, eta=eta_fixed)
        best_loss = best_rmse

        stages = [
            (0.3, 3.0, 61),
            (0.7, 1.3, 41),
            (0.85, 1.15, 31),
        ]
        lam_k = float(self._cfg.prior_lambda_k)

        for lo_fac, hi_fac, n in stages:
            k_min = clamp(best_k * lo_fac, self._cfg.k_loss_min,
                          self._cfg.k_loss_max)
            k_max = clamp(best_k * hi_fac, self._cfg.k_loss_min,
                          self._cfg.k_loss_max)
            for k in linspace(k_min, k_max, n):
                rmse = self.rmse_for(times, obs, powers,
                                     tout=tout, k_loss=k, eta=eta_fixed)
                if not math.isfinite(rmse):
                    continue
                loss = rmse + (lam_k * abs(k - k0) / max(1e-9, abs(k0)))
                if loss < best_loss:
                    best_loss = loss
                    best_rmse = rmse
                    best_k = k

        return float(best_k), float(best_rmse)

    def _fit_eta_only(
        self,
        *,
        times: list[datetime],
        obs: list[float],
        powers: list[float],
        tout: list[float],
        eta0: float,
        k_fixed: float,
    ) -> tuple[float, float]:
        """Fit eta with k_loss held fixed."""
        best_eta = clamp(float(eta0), self._cfg.eta_min, self._cfg.eta_max)
        best_rmse = self.rmse_for(
            times, obs, powers, tout=tout, k_loss=k_fixed, eta=best_eta)
        best_loss = best_rmse

        stages = [
            (0.5, 51),
            (0.25, 41),
            (0.15, 31),
        ]
        lam_e = float(self._cfg.prior_lambda_eta)

        for span, n in stages:
            eta_min = clamp(best_eta - span, self._cfg.eta_min,
                            self._cfg.eta_max)
            eta_max = clamp(best_eta + span, self._cfg.eta_min,
                            self._cfg.eta_max)
            for eta in linspace(eta_min, eta_max, n):
                rmse = self.rmse_for(times, obs, powers,
                                     tout=tout, k_loss=k_fixed, eta=eta)
                if not math.isfinite(rmse):
                    continue
                loss = rmse + (lam_e * abs(eta - eta0))
                if loss < best_loss:
                    best_loss = loss
                    best_rmse = rmse
                    best_eta = eta

        return float(best_eta), float(best_rmse)

    def _refine_joint(
        self,
        *,
        times: list[datetime],
        obs: list[float],
        powers: list[float],
        tout: list[float],
        k0: float,
        eta0: float,
        k_start: float,
        eta_start: float,
    ) -> tuple[float, float, float]:
        """Small joint refinement pass around the two-step solution."""
        best_k = clamp(float(k_start), self._cfg.k_loss_min,
                       self._cfg.k_loss_max)
        best_eta = clamp(float(eta_start), self._cfg.eta_min,
                         self._cfg.eta_max)
        best_rmse = self.rmse_for(
            times, obs, powers, tout=tout, k_loss=best_k, eta=best_eta)
        best_loss = best_rmse

        lam_k = float(self._cfg.prior_lambda_k)
        lam_e = float(self._cfg.prior_lambda_eta)

        k_min = clamp(best_k * 0.85, self._cfg.k_loss_min,
                      self._cfg.k_loss_max)
        k_max = clamp(best_k * 1.15, self._cfg.k_loss_min,
                      self._cfg.k_loss_max)
        eta_min = clamp(best_eta - 0.15, self._cfg.eta_min, self._cfg.eta_max)
        eta_max = clamp(best_eta + 0.15, self._cfg.eta_min, self._cfg.eta_max)

        for k in linspace(k_min, k_max, 13):
            for eta in linspace(eta_min, eta_max, 13):
                rmse = self.rmse_for(times, obs, powers,
                                     tout=tout, k_loss=k, eta=eta)
                if not math.isfinite(rmse):
                    continue
                loss = rmse
                loss += (lam_k * abs(k - k0) / max(1e-9, abs(k0)))
                loss += (lam_e * abs(eta - eta0))
                if loss < best_loss:
                    best_loss = loss
                    best_rmse = rmse
                    best_k = k
                    best_eta = eta

        return float(best_k), float(best_eta), float(best_rmse)

    @staticmethod
    def _smooth_series(values: list[float], *, window: int) -> list[float]:
        """Simple trailing moving average smoothing."""
        if window <= 1 or len(values) < 2:
            return list(values)
        out: list[float] = []
        acc: list[float] = []
        for v in values:
            acc.append(v)
            if len(acc) > window:
                acc.pop(0)
            out.append(float(mean(acc)))
        return out

    def predict_time_to_target_minutes(
        self,
        *,
        cabin_temp_c: float,
        target_temp_c: float,
        outside_temp_c: float,
        k_loss: float,
        eta: float,
        power_w: float | None = None,
        margin_c: float = 0.5,
    ) -> float | None:
        """Compute ETA (minutes) for reaching target temperature."""
        P = power_w if power_w is not None and math.isfinite(
            power_w) else HEATER_POWER_W
        C_eff = HEAT_CAPACITY_J_PER_K * clamp(
            float(self._cfg.mass_factor),
            float(self._cfg.mass_factor_min),
            float(self._cfg.mass_factor_max),
        )

        if cabin_temp_c >= target_temp_c:
            return 0.0
        if k_loss <= 0.0:
            return None

        Tss = outside_temp_c + (eta * P) / k_loss
        if target_temp_c >= (Tss - margin_c):
            return None

        denom = (cabin_temp_c - Tss)
        if denom == 0:
            return None

        ratio = (target_temp_c - Tss) / denom
        if ratio <= 0.0 or ratio >= 1.0:
            return None

        try:
            t_s = -(C_eff / k_loss) * math.log(ratio)
        except Exception:
            logger.debug(
                "predict_time_to_target: math.log failed for ratio=%s", ratio)
            return None

        if not math.isfinite(t_s) or t_s < 0:
            return None
        return t_s / 60.0
