"""add unique indexes for streets and addresses"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_add_unique_indexes"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_street_name_city ON streets(name, city)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_address_street_house ON addresses(street_id, house_number)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_address_street_house")
    op.execute("DROP INDEX IF EXISTS uq_street_name_city")
