from flask_login import LoginManager, UserMixin

import database

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "info"


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.username = row["username"]
        self.email = row["email"]
        self.display_name = row["display_name"]
        self.password_hash = row["password_hash"]
        self.created_at = row["created_at"]


@login_manager.user_loader
def load_user(user_id):
    row = database.get_user_by_id(int(user_id))
    return User(row) if row else None
