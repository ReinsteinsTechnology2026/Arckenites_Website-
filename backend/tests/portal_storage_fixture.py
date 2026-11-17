"""Isolated HTTP/SQL fixture; no lifespan, production data or external services."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base, get_db
from app.main import app
from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.models import User, RoleEnum, Batch, BatchEnrollment, BatchStatusEnum, AuthSession

@compiles(JSONB,'sqlite')
def _json_sqlite(type_, compiler, **kw):return 'JSON'
class QaSession(Session):pass
@event.listens_for(QaSession,'loaded_as_persistent')
def _utc(session, instance):
    for key,value in list(vars(instance).items()):
        if isinstance(value,datetime) and value.tzinfo is None:
            setattr(instance,key,value.replace(tzinfo=timezone.utc))

class Harness(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='portal-storage-test-')
        self.engine=create_engine(f'sqlite:///{Path(self.temp.name)/"test.sqlite"}',connect_args={'check_same_thread':False})
        @event.listens_for(self.engine,'connect')
        def _fk(connection, record):connection.execute('PRAGMA foreign_keys=ON')
        Base.metadata.create_all(self.engine)
        with self.sf() as db:
            users=[User(username=name,full_name=name.title(),role=role,password_hash='unused',is_active=True,must_change_password=False)
                for name,role in [('trainer',RoleEnum.staff),('other-trainer',RoleEnum.staff),('student',RoleEnum.student),('outsider',RoleEnum.student)]]
            db.add_all(users);db.flush();self.ids={u.username:u.id for u in users}
            batch=Batch(name='Batch A',trainer_id=self.ids['trainer'],status=BatchStatusEnum.active)
            other=Batch(name='Batch B',trainer_id=self.ids['other-trainer'],status=BatchStatusEnum.active)
            db.add_all([batch,other]);db.flush();self.batch_id=batch.id;self.other_batch_id=other.id
            db.add_all([BatchEnrollment(batch_id=batch.id,student_id=self.ids['student']),BatchEnrollment(batch_id=other.id,student_id=self.ids['outsider'])]);db.commit()
        self.overrides=app.dependency_overrides.copy();app.dependency_overrides[get_db]=self._db
        self.client=TestClient(app);self.old_limiter=limiter.enabled;limiter.enabled=False
    def tearDown(self):
        self.client.close();app.dependency_overrides.clear();app.dependency_overrides.update(self.overrides)
        limiter.enabled=self.old_limiter;self.engine.dispose();self.temp.cleanup()
    def sf(self):return QaSession(self.engine)
    def _db(self):
        with self.sf() as db:yield db
    def headers(self,name):
        with self.sf() as db:
            user=db.get(User,self.ids[name]);jti=uuid4();token,expires=create_access_token(user.id,user.role.value,jti)
            db.add(AuthSession(id=jti,user_id=user.id,expires_at=expires));db.commit()
        return {'Authorization':f'Bearer {token}'}
