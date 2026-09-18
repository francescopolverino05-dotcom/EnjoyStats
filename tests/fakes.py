"""In-memory player-profile store used by API tests and live simulators."""

from __future__ import annotations

from uuid import UUID

from data_models.player_stats import PlayerMatchProfile


class InMemoryProfileStore:
    """Test double that records upserts without PostgreSQL."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID], PlayerMatchProfile] = {}

    async def upsert_player_profile(
        self,
        match_id: UUID,
        profile: PlayerMatchProfile,
    ) -> UUID:
        if profile.match_id != match_id:
            raise ValueError("profile.match_id does not match argument match_id.")
        stored = PlayerMatchProfile.from_stats(profile)
        self.rows[(profile.player_id, match_id)] = stored
        return stored.stats_id

    async def fetch_player_profile(
        self,
        player_id: UUID,
        match_id: UUID,
    ) -> PlayerMatchProfile | None:
        return self.rows.get((player_id, match_id))
