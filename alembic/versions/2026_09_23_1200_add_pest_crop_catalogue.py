"""Add crop applicability and EPPO identity to pest models."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202609231200"
down_revision: Union[str, None] = "9949dfb9da1e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pest", sa.Column("crops", sa.JSON(), nullable=True))
    op.add_column("pest", sa.Column("eppo_code", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("pest", "eppo_code")
    op.drop_column("pest", "crops")
