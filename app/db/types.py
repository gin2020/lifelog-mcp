"""Database types that keep PostgreSQL production schemas portable to SQLite tests."""

from sqlalchemy import BigInteger, Integer


# PostgreSQL keeps the intended 64-bit integer type. SQLite only treats a
# column declared exactly as INTEGER PRIMARY KEY as a rowid/autoincrement key.
PrimaryKeyInteger = BigInteger().with_variant(Integer, "sqlite")
