"""Encrypted optional Spotify connection; encryption belongs to the service."""

from sqlalchemy import delete, insert, select, update

from ..schema import announcement_spotify


class AnnouncementSpotifyMixin:
    def get_announcement_spotify(self):
        with self._sa_engine.connect() as conn:
            row = conn.execute(select(announcement_spotify)).mappings().first()
            return dict(row) if row else None

    def save_announcement_spotify(self, *, credentials, enabled, devices):
        values = {"credentials": credentials, "enabled": enabled, "devices": devices}
        with self._sa_engine.begin() as conn:
            if not conn.execute(
                update(announcement_spotify).where(announcement_spotify.c.id == 1).values(**values)
            ).rowcount:
                conn.execute(insert(announcement_spotify).values(id=1, **values))

    def delete_announcement_spotify(self):
        with self._sa_engine.begin() as conn:
            conn.execute(delete(announcement_spotify))
