"""Storage probe for tests of the shared private SQLite foundation.

Uses only synthetic rows in the scenario table through the public
``PrivateDatabase.transaction()`` contract; there is no application watchlist
store any more (#95), so access and schema tests need a neutral exerciser.
"""

RECORD = ("synthetic-id", "Synthetische Beobachtung")


class ProbeStore:
    def __init__(self, database):
        self._database = database

    def list(self):
        with self._database.transaction() as connection:
            return tuple(
                connection.execute(
                    "SELECT instrument_id, payload FROM scenario_versions "
                    "ORDER BY instrument_id"
                )
            )

    def get(self, key):
        with self._database.transaction() as connection:
            return connection.execute(
                "SELECT instrument_id, payload FROM scenario_versions "
                "WHERE instrument_id = ?",
                (key,),
            ).fetchone()

    def add(self, record):
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO scenario_versions VALUES (?, 1, ?)", record
            )
        return record

    def update(self, record):
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE scenario_versions SET payload = ? WHERE instrument_id = ?",
                (record[1], record[0]),
            )
        return record

    def remove(self, key):
        with self._database.transaction() as connection:
            connection.execute(
                "DELETE FROM scenario_versions WHERE instrument_id = ?", (key,)
            )
