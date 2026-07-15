# post.py
from sqlite3 import IntegrityError
from sqlalchemy import Text
from __init__ import app, db
from model.user import User

class SavedLocations(db.Model):
    """
    Saved Locations Model
    
    The class represents an individual contribution or discussion within a group.
    
    Attributes:
        id (db.Column): The primary key, an integer representing the unique identifier for the post.
    """
    __tablename__ = 'saved_locations'

    id = db.Column(db.Integer, primary_key=True)
    _user_id = db.Column(db.String(255), db.ForeignKey('users.id'), nullable=False)
    _username = db.Column(db.Integer, nullable=False)
    _user_address = db.Column(db.Integer, nullable=False)
    _user_name = db.Column(db.String(255), nullable=False)

    def __init__(self, user_id, username, user_address, user_name):

        self._username = username
        self._user_id = user_id
        self._user_address = user_address
        self._user_name = user_name

    def __repr__(self):
        """
        The __repr__ method is a special method used to represent the object in a string format.
        Called by the repr(post) built-in function, where post is an instance of the Post class.
        
        Returns:
            str: A text representation of how to create the object.
        """
        return f"SavedLocation(id={self.id}, username={self._username}, user_id={self._user_id}, user_address={self._user_address}, user_name={self._user_name})"

    def create(self):
        """
        The create method adds the object to the database and commits the transaction.
        
        Uses:
            The db ORM methods to add and commit the transaction.
        
        Raises:
            Exception: An error occurred when adding the object to the database.
        """
        try:
            db.session.add(self)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            raise e
        
    def read(self):
        """
        The read method retrieves the object data from the object's attributes and returns it as a dictionary.
        
        Uses:
            The Group.query and User.query methods to retrieve the group and user objects.
        
        Returns:
            dict: A dictionary containing the post data, including user and group names.
        """
        user = User.query.get(self._user_id)
        data = {
            "id": self.id,
            "username": self._username,
            "user_id": self._user_id if user else None,
            "user_address": self._user_address,
            "user_name": self._user_name
        }
        return data
    
    def update(self, inputs):
        """
        The update method commits the transaction to the database.
        
        Uses:
            The db ORM method to commit the transaction.
        
        Raises:
            Exception: An error occurred when updating the object in the database.
        """
        if not isinstance(inputs, dict):
            return self

        user_address = inputs.get("user_address", "")
        user_name = inputs.get("user_name", "")

        if user_address:
            self._user_address = user_address
        if user_name:
            self._user_name = user_name

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            raise e
        return self
    
    def delete(self):
        """
        The delete method removes the object from the database and commits the transaction.
        
        Uses:
            The db ORM methods to delete and commit the transaction.
        
        Raises:
            Exception: An error occurred when deleting the object from the database.
        """    
        try:
            db.session.delete(self)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            raise e

    @staticmethod
    def restore(data):
        sections = {}
        existing_sections = {section._username: section for section in SavedLocations.query.all()}
        for section_data in data:
            _ = section_data.pop('id', None)  # Remove 'id' from section_data
            username = section_data.get("username", None)
            section = existing_sections.pop(username, None)
            if section:
                section.update(section_data)
            else:
                section = SavedLocations(**section_data)
                section.create()
        
        # Remove any extra data that is not in the backup
        for section in existing_sections.values():
            db.session.delete(section)
        
        db.session.commit()
        return sections

def initSavedLocations():
    """
    The initPosts function creates the Post table and adds tester data to the table.
    
    Uses:
        The db ORM methods to create the table.
    
    Instantiates:
        Post objects with tester data.
    
    Raises:
        IntegrityError: An error occurred when adding the tester data to the table.
    """
    with app.app_context():
        """Create database and tables"""