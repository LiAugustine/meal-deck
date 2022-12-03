# pylint: disable=invalid-name
# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=no-member
# pylint: disable=protected-access
# pylint: disable=unspecified-encoding
# pylint: disable=import-error
# pylint: disable=line-too-long

import os
import pathlib
import json
import requests
import flask
import google.auth.transport.requests
from flask import session, abort, request
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from dotenv import find_dotenv, load_dotenv
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from pip._vendor import cachecontrol

from models import db, User, Favorite, Plan
import food_api

load_dotenv(find_dotenv())
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # set environment to HTTPS
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

app = flask.Flask(__name__)

app.secret_key = bytes(os.getenv("session_key"), "utf8")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgres://"):
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config[
        "SQLALCHEMY_DATABASE_URI"
    ].replace("postgres://", "postgresql://")

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

with app.app_context():
    db.create_all()


@login_manager.unauthorized_handler
def unauthorized():
    """
    This function checks if user logged in before accessing content
    """
    return "You must be logged in to access this content.", 403


@login_manager.user_loader
def load_user(user_name):
    """
    This function gets username and send it as query
    """
    return User.query.get(user_name)


secrets = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth?prompt=select_account",  # make user select an account
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uris": [
            "https://mealdeck.onrender.com/auth/google/callback",
            "http://127.0.0.1:5000/callback",
        ],
    }
}
__location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))
f = open(os.path.join(__location__, "client_secrets.json"), "w")
f.write(json.dumps(secrets))
f.close()
client_secrets_file = os.path.join(pathlib.Path(__file__).parent, "client_secrets.json")

flow = Flow.from_client_secrets_file(
    client_secrets_file=client_secrets_file,
    scopes=[
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ],
    # For local deployment, use this line of code:
    # redirect_uri="http://127.0.0.1:5000/callback",
    # For heroku deployment, use this redirect_uri
    redirect_uri="https://mealdeck.onrender.com/callback",
)


@app.route("/login")
def login():
    """
    This function routes to login page
    """
    authorization_url, state = flow.authorization_url()
    session["state"] = state
    return flask.redirect(authorization_url)


@app.route("/callback")
def callback():
    """
    This function handles logins via google account
    """
    flow.fetch_token(authorization_response=request.url)
    if not session["state"] == request.args["state"]:
        abort(500)
    credentials = flow.credentials
    request_session = requests.session()
    cached_session = cachecontrol.CacheControl(request_session)
    token_request = google.auth.transport.requests.Request(session=cached_session)

    id_info = id_token.verify_oauth2_token(
        id_token=credentials._id_token,
        request=token_request,
        audience=GOOGLE_CLIENT_ID,
        clock_skew_in_seconds=10,  # allows system time to be up to 10 seconds off from server time
    )

    name = id_info.get("name")
    google_id = id_info.get("sub")
    session["google_id"] = google_id
    exists = User.query.filter_by(google_id=google_id).first()
    if not exists:
        db.session.add(User(google_id=google_id, username=name))
        db.session.commit()
    user = User.query.filter_by(username=name).first()
    login_user(user)
    return flask.redirect("/meal_deck")


@app.route("/logout")
@login_required
def logout():
    """
    This function redirects to root page
    """
    logout_user()
    return flask.redirect("/")


@app.route("/")
def index():
    """
    This function routes to login page
    """
    return flask.render_template("login.html")


@app.route("/meal_deck")
def meal_deck():
    """
    Main portion of the app after user has logged in
    """
    foodTitle, foodImage = food_api.get_random_foodItem()
    if current_user.is_authenticated:  # if authenticated, go to main page
        return flask.render_template(
            "index.html",
            username=current_user.username,
            foodTitle=foodTitle,
            foodImage=foodImage,
        )
    flask.flash("You must be logged in to access this page!")
    return flask.redirect("/")


@app.route("/get-food")
@login_required
def get_food():
    """
    Function in charge of getting food input and display result of user's search
    """
    search_input = flask.request.args.get("food_input").lower()
    search_term = str(search_input)
    food_recipe = food_api.recipe_call(search_input)
    recipe_titles = food_recipe[0]  # 0th index -> titles
    recipe_images = food_recipe[1]  # 1st index -> images
    recipe_ingredients = food_recipe[2]  # 2nd index -> ingredients
    recipe_instructions = food_recipe[3]  # 3rd index -> instructions
    recipe_count = len(recipe_titles)  # recipe_count for looping purposes

    return flask.render_template(
        "food.html",
        username=current_user.username,
        search_term=search_term,
        recipe_count=recipe_count,
        recipe_titles=recipe_titles,
        recipe_images=recipe_images,
        recipe_ingredients=recipe_ingredients,
        recipe_instructions=recipe_instructions,
        search_success=True,
    )


@app.route("/add_favorite", methods=["POST"])
@login_required
def add_favorite():
    """
    Function which adds a favorite recipe to the database if not already present
    """
    exists = Favorite.query.filter_by(
        google_id=session["google_id"], recipe_name=request.form["recipeName"]
    ).first()  # If not exists then add to db, if already exists, do not add.
    if not exists:
        new_favorite = Favorite(
            google_id=session["google_id"],
            recipe_name=request.form["recipeName"],
        )
        db.session.add(new_favorite)
        db.session.commit()
        flask.flash("You added " + request.form["recipeName"] + " to your favorites!")
        return flask.redirect("/get_favorites")

    flask.flash(
        "You already have " + request.form["recipeName"] + " in your favorites!"
    )
    return flask.redirect("/get_favorites")


@app.route("/add_plan", methods=["POST"])
@login_required
def add_plan():
    """
    Function which adds a recipe to the meal plan
    """
    exists = Plan.query.filter_by(
        google_id=session["google_id"],
        recipe_name=request.form["recipeName"],
        day=request.form["day"],
    ).first()  # If not exists then add to db, if already exists, do not add.
    if not exists:
        new_plan = Plan(
            google_id=session["google_id"],
            recipe_name=request.form["recipeName"],
            day=request.form["day"],
        )
        db.session.add(new_plan)
        db.session.commit()
        flask.flash(
            "You added "
            + request.form["recipeName"]
            + " to the "
            + request.form["day"]
            + " meal plan"
        )
        return flask.redirect("/get_plan")

    flask.flash(
        "You already have "
        + request.form["recipeName"]
        + " in your "
        + request.form["day"]
        + " meal plan"
    )
    return flask.redirect("/get_plan")


@app.route("/delete_favorite", methods=["POST"])
@login_required
def delete_favorite():
    """
    Function which deletes the corresponding favorite recipe from database
    """
    to_delete = Favorite.query.filter_by(
        google_id=session["google_id"], recipe_name=request.form["recipe_name"]
    ).first()
    db.session.delete(to_delete)
    db.session.commit()
    flask.flash("You deleted " + request.form["recipe_name"] + " from your favorites!")
    return flask.redirect("/get_favorites")


@app.route("/delete_plan", methods=["POST"])
@login_required
def delete_plan():
    """
    Function which deletes the corresponding meal plan from database
    """
    google_id = session["google_id"]
    recipe_name = request.form["recipe_name"]
    day = request.form["day"]
    to_delete = Plan.query.filter_by(
        google_id=google_id,
        recipe_name=recipe_name,
        day=day,
    ).first()
    # print(to_delete)
    db.session.delete(to_delete)
    db.session.commit()
    flask.flash("You deleted " + request.form["recipe_name"])
    return flask.redirect("/get_plan")


@app.route("/get_favorites")
@login_required
def get_favorites():
    """
    Function which handles the page with the user's favorite recipes
    """
    favorites = Favorite.query.filter_by(google_id=session["google_id"]).all()
    return flask.render_template(
        "favorites.html",
        username=current_user.username,
        favorites=favorites,
    )


@app.route("/get_plan")
@login_required
def get_plan():
    """
    Function which handles the page with the user's weekly meal plan
    """
    sunday = Plan.query.filter_by(google_id=session["google_id"], day="sunday").all()
    monday = Plan.query.filter_by(google_id=session["google_id"], day="monday").all()
    tuesday = Plan.query.filter_by(google_id=session["google_id"], day="tuesday").all()
    wednesday = Plan.query.filter_by(
        google_id=session["google_id"], day="wednesday"
    ).all()
    thursday = Plan.query.filter_by(
        google_id=session["google_id"], day="thursday"
    ).all()
    friday = Plan.query.filter_by(google_id=session["google_id"], day="friday").all()
    saturday = Plan.query.filter_by(
        google_id=session["google_id"], day="saturday"
    ).all()
    return flask.render_template(
        "plan.html",
        username=current_user.username,
        sunday=sunday,
        monday=monday,
        tuesday=tuesday,
        wednesday=wednesday,
        thursday=thursday,
        friday=friday,
        saturday=saturday,
    )


# For local deployment, use this app.run() line:
# app.run(use_reloader=True, debug=True)

# For heroku deployment, uncomment the below two:

port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port, debug=True)
