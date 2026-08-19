"""add programs and program_enrollments; link batches to programs

Revision ID: a7b3f9d1e6c4
Revises: f3a7c1e9b2d5
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b3f9d1e6c4'
down_revision: Union[str, None] = 'f3a7c1e9b2d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# slug matches ProgramEnum in app/models/student.py exactly, so this table
# can be joined against the existing student_profiles.program column by
# value without touching that column or the student onboarding flow at all.
SEED_PROGRAMS = [
    dict(slug='official_certification', name='Official Certification Program', code='OCP-01', category='Certification',
         description='Certification-focused technical training designed to help students prepare for industry-recognized certifications.'),
    dict(slug='corporate_training', name='Corporate Training Program', code='CTP-01', category='Corporate',
         description='Customized technical training delivered for corporate teams to upskill employees on current industry tools and practices.'),
    dict(slug='institutional', name='Institutional Program', code='INS-01', category='Institutional',
         description='Structured training partnership delivered directly through academic institutions to prepare students for the industry.'),
    dict(slug='placement_training', name='Placement Training Program', code='PTP-01', category='Placement',
         description='Focused training and mock assessments designed to get students placement-ready for technical interviews and hiring drives.'),
    dict(slug='trainers_program', name='Arckenites Trainers Program', code='ATP-01', category='Trainers',
         description='A train-the-trainer track that prepares experienced professionals to become Arckenites-certified technical trainers.'),
    dict(slug='internship', name='Internship Program', code='INT-01', category='Internship',
         description='Hands-on internship track where students work on real projects under mentorship to gain practical industry experience.'),
    dict(slug='interview_crack', name="Interview 'n' Crack Program", code='INC-01', category='Placement',
         description="Focused interview preparation with mock interviews and feedback to help students walk in confident and walk out with an offer."),
    dict(slug='job_assist', name='Arckenites Job Assist Program', code='JAP-01', category='Placement',
         description='Hands-on support connecting trained students to relevant job openings and guiding them through the hiring process.'),
    dict(slug='college_projects', name='College Projects Program', code='CPP-01', category='Academic',
         description='Guided support for college students building academic mini and major projects with mentor supervision.'),
]


def upgrade() -> None:
    program_mode_enum = sa.Enum('online', 'offline', 'hybrid', name='program_mode_enum')
    program_status_enum = sa.Enum('active', 'inactive', name='program_status_enum')
    enrollment_status_enum = sa.Enum(
        'pending', 'approved', 'allocated', 'active', 'completed', 'withdrawn', 'rejected',
        name='enrollment_status_enum',
    )

    op.create_table(
        'programs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('code', sa.String(length=40), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(length=120), nullable=True),
        sa.Column('duration', sa.String(length=120), nullable=True),
        sa.Column('eligibility', sa.Text(), nullable=True),
        sa.Column('objectives', sa.Text(), nullable=True),
        sa.Column('max_capacity', sa.Integer(), nullable=True),
        sa.Column('mode', program_mode_enum, nullable=False),
        sa.Column('status', program_status_enum, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
        sa.UniqueConstraint('code'),
    )
    op.create_index(op.f('ix_programs_slug'), 'programs', ['slug'], unique=True)

    op.create_table(
        'program_enrollments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('program_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('status', enrollment_status_enum, nullable=False),
        sa.Column('enrolled_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['program_id'], ['programs.id'], ),
        sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('program_id', 'student_id'),
    )
    op.create_index(op.f('ix_program_enrollments_program_id'), 'program_enrollments', ['program_id'], unique=False)
    op.create_index(op.f('ix_program_enrollments_student_id'), 'program_enrollments', ['student_id'], unique=False)

    op.add_column('batches', sa.Column('program_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_batches_program_id'), 'batches', ['program_id'], unique=False)
    op.create_foreign_key('batches_program_id_fkey', 'batches', 'programs', ['program_id'], ['id'])

    programs_table = sa.table(
        'programs',
        sa.column('slug', sa.String), sa.column('name', sa.String), sa.column('code', sa.String),
        sa.column('category', sa.String), sa.column('description', sa.Text),
        sa.column('mode', program_mode_enum), sa.column('status', program_status_enum),
    )
    op.bulk_insert(programs_table, [
        {**row, 'mode': 'offline', 'status': 'active'} for row in SEED_PROGRAMS
    ])

    # Backfill: any student who already picked a program via onboarding
    # (student_profiles.program) becomes an "active" enrollment against the
    # matching program row, so existing data shows up immediately instead of
    # every program looking freshly empty.
    op.execute("""
        INSERT INTO program_enrollments (program_id, student_id, status, enrolled_at)
        SELECT p.id, sp.user_id, 'active', COALESCE(sp.updated_at, now())
        FROM student_profiles sp
        JOIN programs p ON p.slug = sp.program::text
        WHERE sp.program IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_constraint('batches_program_id_fkey', 'batches', type_='foreignkey')
    op.drop_index(op.f('ix_batches_program_id'), table_name='batches')
    op.drop_column('batches', 'program_id')

    op.drop_index(op.f('ix_program_enrollments_student_id'), table_name='program_enrollments')
    op.drop_index(op.f('ix_program_enrollments_program_id'), table_name='program_enrollments')
    op.drop_table('program_enrollments')

    op.drop_index(op.f('ix_programs_slug'), table_name='programs')
    op.drop_table('programs')

    sa.Enum(name='enrollment_status_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='program_status_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='program_mode_enum').drop(op.get_bind(), checkfirst=True)
