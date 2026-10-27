"""Evidence for announcements through the owner's existing Cast speaker group.

A group target plays one synchronized stream on the group receiver; the
group's own playback report is the only playback evidence (``evidence_scope =
'group'``). For each configured member we record only what is observable:

- ``availability``: the member's own receiver answered a probe at dispatch
  (``available``/``unavailable``/``unknown``);
- ``in_group``: the group listed the member's Cast UUID among its current
  members (``None`` when the group reported no membership or the member has
  no configured UUID);
- ``left_group_during_playback``: the member disappeared from the reported
  membership while the announcement played (``None`` when not reported).

Nothing here claims that a member was audible or that anyone heard it, and no
member is ever played separately: a member known to be missing makes the
delivery ``partial``, which stays fallback-eligible instead of replaying the
group (and its successful members) or substituting an unsynchronized stream.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .schedules import iso
from .transport import SpeakerProbe, TransportError

logger = logging.getLogger(__name__)

MEMBER_UNAVAILABLE = "member_unavailable"


@dataclass(frozen=True)
class GroupProbe(SpeakerProbe):
    """Group receiver probe combined with its members' own probes.

    ``state`` is the group's state, raised to ``playing``/``unknown`` when a
    reachable member is playing or unclear (music on one JBL must still make
    an ordinary reminder wait).
    """

    members: tuple[dict, ...] = ()


def combine(group: SpeakerProbe, member_probes: list[tuple]) -> GroupProbe:
    """``member_probes``: (key, name, device_id, SpeakerProbe | None) per member."""
    evidence = []
    states = []
    for key, name, device_id, probe in member_probes:
        if probe is None:
            availability = "unknown"
        else:
            availability = "unavailable" if probe.state == "unavailable" else "available"
            if availability == "available":
                states.append(probe.state)
        in_group = None
        if group.member_ids is not None and device_id:
            in_group = device_id in group.member_ids
        evidence.append(
            {
                "key": key,
                "name": name,
                "availability": availability,
                "in_group": in_group,
                "left_group_during_playback": None,
            }
        )
    state = group.state
    if state != "unavailable":
        if state == "playing" or "playing" in states:
            state = "playing"
        elif state == "unknown" or "unknown" in states:
            state = "unknown"
    return GroupProbe(
        state=state,
        volume=group.volume,
        device_id=group.device_id,
        member_ids=group.member_ids,
        members=tuple(evidence),
    )


def observe_playback(evidence: list[dict], member_ids, device_ids: dict) -> list[dict] | None:
    """Merge membership reported during playback; None when nothing changed.

    ``device_ids`` maps member key -> configured Cast UUID. Leaving is sticky:
    a member seen missing once stays recorded as having left.
    """
    if member_ids is None:
        return None
    updated = []
    for entry in evidence:
        entry = dict(entry)
        device_id = device_ids.get(entry["key"])
        if device_id and entry["in_group"] is not False:
            entry["left_group_during_playback"] = bool(
                entry["left_group_during_playback"] or device_id not in member_ids
            )
        updated.append(entry)
    return updated if updated != evidence else None


def affected_members(evidence: list[dict] | None) -> list[dict]:
    """Members known not to have taken part (empty when nothing is known)."""
    return [
        entry
        for entry in evidence or ()
        if entry["availability"] == "unavailable"
        or entry["in_group"] is False
        or entry["left_group_during_playback"]
    ]


class CastDiscoveryMixin:
    """Read-only discovery of Cast devices/groups at the configured hosts.

    The owner requests it from the page; ``tick()`` runs it (the request path
    never waits on the network). It lists what the receivers report, probes
    every configured target so the selector shows current availability, and
    suggests ``ANNOUNCEMENT_TARGETS`` entries with stable Cast UUIDs. It never
    creates, joins or reconfigures a Google Home group.
    """

    _discovery: dict | None = None

    def discovery_status(self) -> dict:
        if self._discovery is None:
            return {"status": "idle", "devices": [], "suggested_targets": [], "reason": None}
        return dict(self._discovery)

    def request_discovery(self) -> dict:
        with self._lock:
            if self._discovery is None or self._discovery["status"] != "requested":
                self._discovery = {
                    "status": "requested",
                    "requested_at": iso(self._clock()),
                    "finished_at": None,
                    "reason": None,
                    "devices": [],
                    "suggested_targets": [],
                }
        logger.info("Cast discovery requested")
        self._wake.set()
        return self.discovery_status()

    def _run_discovery(self) -> None:
        if self._discovery is None or self._discovery["status"] != "requested":
            return
        hosts = sorted({t.host for t in self.config.targets if t.host})
        devices, reason = [], None
        if not self.transport.status().get("available"):
            reason = "cast_unavailable"
        else:
            try:
                devices = list(self.transport.discover(hosts))
            except TransportError as exc:
                reason = exc.reason
            except Exception:
                logger.exception("Cast discovery raised unexpectedly")
                reason = "discovery_error"
            for target in self.config.targets:
                if target.configured:
                    self._probe_target(target)
        self._discovery.update(
            status="failed" if reason else "finished",
            reason=reason,
            finished_at=iso(self._clock()),
            devices=devices,
            suggested_targets=self._suggest_targets(devices),
        )
        groups = sum(1 for d in devices if d.get("cast_type") == "group")
        logger.info(
            "Cast discovery %s: %s devices (%s groups) reason=%s",
            self._discovery["status"],
            len(devices),
            groups,
            reason,
        )

    def _suggest_targets(self, devices: list[dict]) -> list[dict]:
        """Configured targets completed with discovered identities (never invented)."""
        groups = [d for d in devices if d.get("cast_type") == "group"]
        suggested = []
        for target in self.config.targets:
            entry = {"key": target.key, "name": target.name}
            if target.is_group:
                found = [g for g in groups if g.get("device_id") == target.device_id]
                if not found and not target.configured and len(groups) == 1:
                    found = groups  # the one existing group; the owner confirms by saving
                match = found[0] if found else None
                entry.update(kind="group", host=target.host, port=target.port)
                entry["device_id"] = target.device_id
                if match:
                    entry.update(
                        host=match.get("host"), port=match.get("port"), device_id=match["device_id"]
                    )
                entry["members"] = list(target.members)
            else:
                match = next(
                    (
                        d
                        for d in devices
                        if d.get("cast_type") != "group"
                        and d.get("host") == target.host
                        and d.get("port", 8009) == target.port
                    ),
                    None,
                )
                entry["host"] = target.host
                if target.port != 8009:
                    entry["port"] = target.port
                entry["device_id"] = (match or {}).get("device_id") or target.device_id
            suggested.append(entry)
        return suggested
