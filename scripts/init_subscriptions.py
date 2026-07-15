"""
Script to initialize subscription tables in the existing database.
Run this once after adding the subscription system.

Usage:
    python scripts/init_subscriptions.py
"""
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from __init__ import app, db
# Import all models so SQLAlchemy knows about them
from model.user import User
from model.subscription import Subscription, SubscriptionRequest, PaymentHistory, RouteUsage

def init_subscription_tables():
    """Create subscription tables if they don't exist."""
    with app.app_context():
        # db.create_all() only creates tables that don't exist yet
        # It won't modify or drop existing tables
        db.create_all()
        
        print("✓ Subscription tables created successfully!")
        print("  - subscriptions")
        print("  - subscription_requests")
        print("  - payment_history")
        print("  - route_usage")

if __name__ == "__main__":
    init_subscription_tables()
