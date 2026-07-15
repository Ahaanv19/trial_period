"""
Analysis model — persistence layer for TextBack.

Every message the user runs through the "Think Before You Send" analyzer is
stored here so the app has a durable, server-side history (in addition to the
browser's localStorage cache). Uses the project's standard SQLite/SQLAlchemy
setup so it works locally with zero external services.
"""
import json
from datetime import datetime

from __init__ import app, db


class Analysis(db.Model):
    """A single message analysis result."""
    __tablename__ = 'analyses'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    _message = db.Column('message', db.Text, nullable=False)
    _context = db.Column('context', db.String(32), nullable=False, default='friend')
    _tone_bias = db.Column('tone_bias', db.String(64), nullable=True)
    # Full analyzer response, stored as JSON text so the schema can evolve
    # without a migration.
    _result = db.Column('result', db.Text, nullable=False)
    # Denormalized headline numbers for cheap history sorting/filtering.
    _safe_to_send = db.Column('safe_to_send', db.Integer, nullable=True)
    _reply_probability = db.Column('reply_probability', db.Integer, nullable=True)
    _send_decision = db.Column('send_decision', db.String(16), nullable=True)
    _created_at = db.Column('created_at', db.DateTime, default=datetime.utcnow)

    def __init__(self, message, context, result, tone_bias=None):
        self._message = message
        self._context = context or 'friend'
        self._tone_bias = tone_bias
        self._result = json.dumps(result)
        self._safe_to_send = result.get('safeToSend')
        self._reply_probability = result.get('replyProbability')
        self._send_decision = result.get('sendDecision')

    def create(self):
        """Persist this analysis; best-effort so a DB hiccup never breaks the
        user-facing response."""
        try:
            db.session.add(self)
            db.session.commit()
            return self
        except Exception as e:
            db.session.rollback()
            raise e

    def read(self):
        """Return a JSON-serializable dict for the history API."""
        try:
            result = json.loads(self._result)
        except Exception:
            result = {}
        return {
            'id': self.id,
            'message': self._message,
            'context': self._context,
            'toneBias': self._tone_bias,
            'safeToSend': self._safe_to_send,
            'replyProbability': self._reply_probability,
            'sendDecision': self._send_decision,
            'timestamp': int(self._created_at.timestamp() * 1000) if self._created_at else None,
            'result': result,
        }


def initAnalyses():
    """Create the analyses table. Called by the `generate_data` CLI command."""
    with app.app_context():
        db.create_all()
        print("Created 'analyses' table (or it already existed).")
