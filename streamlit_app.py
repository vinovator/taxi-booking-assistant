"""Taxi Booking Assistant web UI: three personas side by side.

- Customer: chats with the booking assistant.
- Taxi company representative: approves or rejects short-notice requests.
- Company admin: watches bookings, customers, the taxi catalog and approvals.

This is a demo with sample data, so there's no sign-in.
Run it from the project folder with:  streamlit run streamlit_app.py
"""
import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from booking_concierge.agent import root_agent  # also creates taxi.db with the seed data on first run
from booking_concierge.data import database as db

APP_NAME = "booking_concierge"
ASSISTANT_AVATAR = "🚕"
CUSTOMER_AVATAR = ":material/person:"
WELCOME = ("Hi there! I'm the taxi booking assistant. I can book a taxi, or change or cancel one you've "
           "already booked.\n\nTo get started, are you a **business** or a **personal** customer?")
SUGGESTIONS = {  # shown as chips before the first message
    "I'm a personal customer": ":material/person:",
    "I'm a business customer": ":material/business_center:",
    "I'd like to change a booking": ":material/edit_calendar:",
}
PERSONAS = {  # the label at the top of each panel: who the panel is for
    "customer": ":blue[:material/person: **Customer**]",
    "representative": ":orange[:material/support_agent: **Taxi company representative**]",
    "admin": ":violet[:material/admin_panel_settings: **Company admin**]",
}
ACTION_STYLE = {  # badge icon and colour for each kind of approval request
    "New booking": (":material/add_circle:", "blue"),
    "Change": (":material/edit_calendar:", "violet"),
    "Cancellation": (":material/event_busy:", "red"),
}
STATUS_LABEL = {
    "confirmed": "🟢 Confirmed", "cancelled": "⚪ Cancelled", "completed": "🔵 Completed",
    "waiting": "🟠 Waiting", "approved": "🟢 Approved", "rejected": "🔴 Rejected",
}
TAXI_PICTURES = Path(__file__).parent / "images"  # a picture for each taxi type, named after it, e.g. suv.svg

st.set_page_config(page_title="Taxi Booking Assistant", page_icon="🚕", layout="wide")

STYLE = """
<style>
/* 4rem at the top clears Streamlit's top bar, which shows Fork and GitHub buttons on Streamlit Community Cloud */
[data-testid="stMainBlockContainer"] { padding: 4rem 1.5rem 1rem; max-width: 1720px; }

/* Header banner: one slim row, so the panels get most of the screen */
.tba-hero { display: flex; align-items: center; justify-content: space-between; gap: .75rem 1.5rem;
  padding: .75rem 1.25rem; border-radius: 16px; color: #FFFFFF;
  background: radial-gradient(900px 200px at 0% 0%, rgba(250, 204, 21, .20), transparent 60%),
              linear-gradient(115deg, #0B1220 0%, #13254B 45%, #1D4ED8 100%);
  box-shadow: 0 8px 24px rgba(15, 23, 42, .20); }
.tba-brand { display: flex; align-items: center; gap: .85rem; min-width: 0; }
.tba-logo { flex: none; width: 40px; height: 40px; border-radius: 12px; display: grid; place-items: center;
  font-size: 22px; background: linear-gradient(145deg, #FDE047, #F59E0B); box-shadow: 0 4px 12px rgba(245, 158, 11, .35); }
.tba-title { font-size: 1.2rem; font-weight: 700; letter-spacing: -.01em; line-height: 1.2; }
.tba-subtitle { margin-top: .1rem; font-size: .85rem; color: rgba(255, 255, 255, .75);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tba-chips { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .4rem; }
.tba-chip { padding: .3rem .75rem; border-radius: 999px; font-size: .8rem; white-space: nowrap;
  background: rgba(255, 255, 255, .10); border: 1px solid rgba(255, 255, 255, .18); }
.tba-dot { display: inline-block; width: 8px; height: 8px; margin-right: .45rem; border-radius: 50%;
  background: #22C55E; box-shadow: 0 0 0 3px rgba(34, 197, 94, .25); vertical-align: middle; }

/* Panels: white cards on the grey page */
.st-key-panel_chat, .st-key-panel_representative, .st-key-panel_admin {
  background: #FFFFFF; border-radius: 20px !important;
  box-shadow: 0 1px 2px rgba(15, 23, 42, .05), 0 10px 30px rgba(15, 23, 42, .06); }

/* The chat panel fills the screen below the banner, so the chat box is always in view. Wider than a phone, where
   the panels sit side by side, the other two panels do the same. Each panel's main area (chat_messages,
   representative_body or admin_body) takes the space that's left and scrolls inside the panel.
   Streamlit can't size a scrolling container to the screen, hence the CSS. */
.st-key-panel_chat { flex: none !important; height: calc(100dvh - 10.5rem); min-height: 26rem; }
[data-testid="stLayoutWrapper"]:has(> .st-key-chat_messages) { flex: 1 1 0 !important; min-height: 0; height: auto !important; }
@media (min-width: 641px) {
  .st-key-panel_representative, .st-key-panel_admin { flex: none !important; height: calc(100dvh - 10.5rem); min-height: 26rem; }
  [data-testid="stLayoutWrapper"]:has(> .st-key-representative_body, > .st-key-admin_body) {
    flex: 1 1 0 !important; min-height: 0; height: auto !important; }
}
@media (max-width: 1200px) { .tba-chips { display: none; } }  /* the panels show the same numbers */
@media (max-width: 640px) {  /* phones: keep the banner to its name, and clear Streamlit Cloud's corner badges */
  [data-testid="stMainBlockContainer"] { padding-bottom: 4rem; }
  .st-key-panel_chat { height: calc(100dvh - 12rem); }
  .tba-subtitle { display: none; }
}

/* Header rows and metric rows stay on one line on narrow panels; long text wraps inside them instead. */
[class*="st-key-row_"] { flex-wrap: nowrap !important; }

/* Approval request cards */
[class*="st-key-request_"] { background: #FFFBF2; border-left: 4px solid #F59E0B !important; }

/* Taxi catalog in the chat: one row of cards that scrolls sideways when the chat is too narrow for them all */
[class*="st-key-catalog_"] { flex-wrap: nowrap !important; overflow-x: auto; scroll-snap-type: x mandatory;
  padding-bottom: .5rem; }
[class*="st-key-catalog_"] > * { flex-shrink: 0 !important; scroll-snap-align: start; }
[class*="st-key-catalog_"]::-webkit-scrollbar { height: 6px; }  /* a thin scrollbar that stays visible */
[class*="st-key-catalog_"]::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 3px; }
[class*="st-key-taxi_"] { background: #FFFFFF; }
[class*="st-key-taxi_"]:hover { border-color: #F59E0B !important; }

/* The invisible helper that keeps the chat scrolled to the newest message */
[data-testid="stLayoutWrapper"]:has(> .st-key-chat_autoscroll), .st-key-chat_autoscroll { display: none; }
</style>
"""

# Keeps the chat box scrolled to the newest message. Streamlit has no setting for this, so a tiny script
# (in an invisible component) scrolls the box to the bottom whenever messages are added to it.
AUTOSCROLL = """
<script>
const page = window.parent.document;
function scrollChatToBottom() {
  page.querySelectorAll(".st-key-chat_messages").forEach((box) => {
    const scroller = [box, ...box.querySelectorAll("div")].find((el) => el.scrollHeight > el.clientHeight + 4);
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  });
}
new MutationObserver((changes) => {
  if (changes.some((change) => change.target.closest && change.target.closest(".st-key-chat_messages"))) {
    scrollChatToBottom();
  }
}).observe(page.body, { childList: true, subtree: true });
scrollChatToBottom();
</script>
"""


def secret(name: str) -> str | None:
    """A value from Streamlit's secrets (.streamlit/secrets.toml, or the Secrets settings once deployed)."""
    try:
        return st.secrets.get(name)
    except Exception:  # there's no secrets file at all
        return None


# The API keys, for Gemini and for Google Maps' distances, come from Streamlit's secrets when deployed, or from
# booking_concierge/.env locally. Secrets win; .env only fills in what they don't set.
for name in ("GOOGLE_API_KEY", "GOOGLE_MAPS_API_KEY"):
    if secret(name):
        os.environ[name] = secret(name)
load_dotenv(Path(__file__).parent / APP_NAME / ".env")
os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "0")


@st.cache_resource
def get_runners() -> dict[str, Runner]:
    """A runner for each agent, all sharing the same saved conversations (data/sessions.db).

    Customer messages go through the root agent's runner. A representative's decision goes through the runner of
    the agent that asked for approval, because ADK can only resume a paused tool with the agent that owns it.
    """
    sessions = SqliteSessionService(str(db.DATA_DIR / "sessions.db"))
    return {agent.name: Runner(app_name=APP_NAME, agent=agent, session_service=sessions)
            for agent in [root_agent, *root_agent.sub_agents]}


runners = get_runners()
sessions = runners[root_agent.name].session_service


# --------- Talking to the agent ---------

def text_message(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def decision_message(confirmation_call_id: str, approved: bool, message: str) -> types.Content:
    """A representative's decision, in the reply format ADK expects for a tool confirmation."""
    return types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
        id=confirmation_call_id,
        name="adk_request_confirmation",
        response={"confirmed": approved, "payload": {"message": message}},
    ))])


def send(user_id: str, session_id: str, message: types.Content, agent_name: str = root_agent.name) -> None:
    """Run an agent in a conversation, and add any approval request it raises to the representative's list."""
    async def run() -> None:
        async for event in runners[agent_name].run_async(user_id=user_id, session_id=session_id,
                                                         new_message=message):
            for call in event.get_function_calls():
                if call.name == "adk_request_confirmation":
                    confirmation = call.args.get("toolConfirmation") or {}
                    details = confirmation.get("payload") or {}
                    db.add_approval_request(session_id, user_id, call.id, details.get("action", "Request"),
                                            confirmation.get("hint", ""), details)

    asyncio.run(run())


def conversation_events(user_id: str, session_id: str) -> list | None:
    """The saved events of a conversation, or None if it no longer exists."""
    session = asyncio.run(sessions.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id))
    return None if session is None else session.events


def message_text(event) -> str:
    """The visible text of an event; tool calls, tool results and model thoughts are left out."""
    parts = event.content.parts if event.content and event.content.parts else []
    return "".join(part.text for part in parts if part.text and not part.thought).strip()


def escape_dollars(text: str) -> str:
    """Streamlit's markdown shows text between two $ signs as maths. Our prices are in pounds, but a customer or
    the model can still write $ (say, "$40 or $50"), so escape it to show the text as it is."""
    return text.replace("$", "\\$")


def taxi_options(event) -> list[dict] | None:
    """The taxis offered in this event, if it holds the result of the list_taxi_types tool, otherwise None.

    ADK saves every tool result in the conversation. A tool that returns a list is saved as {"result": [...]}.
    """
    for response in event.get_function_responses():
        if response.name == "list_taxi_types" and isinstance((response.response or {}).get("result"), list):
            return response.response["result"]
    return None


# --------- Small helpers for the layout ---------

def ago(timestamp: str) -> str:
    """How long ago a saved time was, e.g. "5 min ago"."""
    minutes = int((datetime.now() - datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    return f"{minutes} min ago" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min ago"


def minutes_until(pickup_at: str) -> int:
    """Whole minutes until a pickup saved as "YYYY-MM-DD HH:MM" (zero or less once it has passed)."""
    return int((datetime.strptime(pickup_at, "%Y-%m-%d %H:%M") - datetime.now()).total_seconds() // 60)


def countdown(pickup_at: str) -> str:
    """Time left until a pickup, e.g. "Pickup in 3 h 05 min"."""
    minutes = minutes_until(pickup_at)
    if minutes <= 0:
        return "Pickup time has passed"
    hours, minutes = divmod(minutes, 60)
    return f"Pickup in {hours} h {minutes:02d} min" if hours else f"Pickup in {minutes} min"


def panel_header(persona: str, icon: str, title: str, caption: str, badge: tuple[str, str], button: dict) -> None:
    """The top of a panel: who it's for with a status badge, the title with a small icon button, and a one-line
    description. `button` holds the st.button arguments for the icon button; its `help` is the tooltip."""
    with st.container(horizontal=True, vertical_alignment="center", key=f"row_status_{persona}"):
        st.caption(PERSONAS[persona])
        st.badge(badge[0], color=badge[1])
    with st.container(horizontal=True, vertical_alignment="center", key=f"row_title_{persona}"):
        st.markdown(f"#### {icon} {title}")
        st.button("", type="tertiary", **button)
    st.caption(caption)


# --------- Customer ---------

def customer_conversation() -> tuple[str, str]:
    """This browser tab's conversation with the assistant, created on first use."""
    if "session_id" not in st.session_state:
        user_id = f"web-{uuid.uuid4().hex[:8]}"
        session = asyncio.run(sessions.create_session(app_name=APP_NAME, user_id=user_id))
        st.session_state.user_id, st.session_state.session_id = user_id, session.id
    return st.session_state.user_id, st.session_state.session_id


def taxi_picture(taxi_type: str) -> Path | None:
    """The picture for a taxi type: a file in images/ named after it. A photo (suv.png, suv.jpg) beats the
    drawing (suv.svg)."""
    pictures = sorted(TAXI_PICTURES.glob(f"{taxi_type.lower()}.*"), key=lambda path: path.suffix == ".svg")
    return pictures[0] if pictures else None


def taxi_cards(options: list[dict], key: str, can_choose: bool) -> None:
    """The taxi catalog as cards: picture, name, passengers and price per mile.

    Choosing a taxi is the same as typing "I'd like the SUV." in the chat: chat_panel sends the message.
    """
    with st.container(horizontal=True, key=f"catalog_{key}"):  # one row; the CSS above lets it scroll sideways
        for taxi in options:
            name = taxi["taxi_type"]
            with st.container(border=True, width=116, key=f"taxi_{key}_{name}"):
                picture = taxi_picture(name)
                if picture:
                    st.image(picture, use_container_width=True)
                else:
                    st.markdown("## 🚕")
                st.markdown(f"**{name}**  \n:material/group: Up to {taxi['max_passengers']}  \n"
                            f"£{taxi['rate_per_mile']:.2f} a mile")
                if can_choose and st.button("Choose", key=f"choose_{key}_{name}", width="stretch"):
                    st.session_state.chosen_taxi = f"I'd like the {name}."
                    st.rerun()  # reruns the whole page, not just the chat fragment, so chat_panel sends it


@st.fragment(run_every=5)
def chat_history(user_id: str, session_id: str) -> None:
    """The conversation so far. Refreshes every 5 seconds, so a representative's decision shows up.

    When the assistant has shown the taxi catalog (the list_taxi_types tool), the catalog appears as cards.
    Only the newest set of cards can be chosen from.
    """
    events = conversation_events(user_id, session_id)
    if events is None:  # the saved conversations were reset, so start a new one
        st.session_state.pop("session_id", None)
        st.rerun()

    newest_catalog = next((event.id for event in reversed(events) if taxi_options(event)), None)
    shown = 0
    for event in events:
        if text := message_text(event):
            if event.author == "user":
                st.chat_message("user", avatar=CUSTOMER_AVATAR).markdown(escape_dollars(text))
            else:
                st.chat_message("assistant", avatar=ASSISTANT_AVATAR).markdown(escape_dollars(text))
            shown += 1
        elif options := taxi_options(event):
            with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
                taxi_cards(options, key=event.id, can_choose=event.id == newest_catalog)
            shown += 1
    if not shown:
        st.chat_message("assistant", avatar=ASSISTANT_AVATAR).markdown(WELCOME)

    if any(request["session_id"] == session_id for request in db.list_approval_requests("waiting")):
        st.badge("Waiting for our team's approval", icon=":material/hourglass_top:", color="orange")


def new_conversation() -> None:
    st.session_state.pop("session_id", None)


def chat_panel() -> None:
    with st.container(border=True, key="panel_chat"):
        panel_header("customer", ":material/forum:", "Chat with the assistant",
                     "Book a taxi, or change or cancel one.", ("Online", "green"),
                     dict(icon=":material/restart_alt:", help="New chat: start again as a different customer",
                          key="new_chat", on_click=new_conversation))
        user_id, session_id = customer_conversation()

        # 480 px on phones; on wider screens the CSS above makes it fill the panel.
        messages = st.container(height=480, border=False, key="chat_messages")
        with messages:
            chat_history(user_id, session_id)
        with st.container(key="chat_autoscroll"):
            components.html(AUTOSCROLL, height=0)
        if error := st.session_state.pop("chat_error", None):
            st.error(error, icon=":material/error:")

        suggestion = None
        if not any(message_text(event) for event in conversation_events(user_id, session_id) or []):
            with st.container(horizontal=True, gap="small"):
                for number, (text, icon) in enumerate(SUGGESTIONS.items()):
                    if st.button(text, key=f"suggestion_{number}", icon=icon):
                        suggestion = text

        # A suggestion chip or a taxi card's Choose button sends its message just as if the customer had typed it.
        prompt = st.chat_input("Type your message…") or suggestion or st.session_state.pop("chosen_taxi", None)
        if prompt:
            with messages:
                pending = st.empty()  # shows the message straight away; cleared once the reply is saved
                with pending.container():
                    st.chat_message("user", avatar=CUSTOMER_AVATAR).markdown(escape_dollars(prompt))
                    with st.spinner("The assistant is replying…"):
                        try:
                            send(user_id, session_id, text_message(prompt))
                        except Exception as error:  # for example, the Gemini quota has run out
                            st.session_state.chat_error = f"The assistant couldn't reply: {str(error)[:500]}"
                pending.empty()
            st.rerun()


# --------- Taxi company representative ---------

def requesting_agent(request: dict) -> str:
    """The agent that asked for this approval, found from the conversation."""
    for event in conversation_events(request["user_id"], request["session_id"]) or []:
        if any(call.id == request["confirmation_call_id"] for call in event.get_function_calls()):
            return event.author
    return root_agent.name


def decide(request: dict, approved: bool, message: str) -> None:
    """Record a representative's decision, then send it into the customer's conversation, where ADK resumes
    the paused tool.

    - The decision is recorded first, so each request is answered only once. ADK raises an error for a second
      answer to the same request, which a retry after a failure, or two representatives at once, would send.
    - It goes to the agent that asked for approval. The customer may have moved on to another agent since
      then, and ADK can only resume a paused tool with the agent that owns it.
    """
    number, outcome = request["request_id"], "approved" if approved else "rejected"
    if not db.decide_approval_request(number, approved, message):
        st.session_state.representative_error = f"Request #{number} has already been decided."
        return
    try:
        send(request["user_id"], request["session_id"],
             decision_message(request["confirmation_call_id"], approved, message),
             agent_name=requesting_agent(request))
    except Exception as error:  # for example, the Gemini quota has run out
        st.session_state.representative_error = (f"Request #{number} was {outcome}, but the assistant couldn't "
                                                 f"reply to the customer: {str(error)[:500]}")
        return
    st.session_state.flash = (f"Request #{number} {outcome}",
                              ":material/check_circle:" if approved else ":material/block:")


def request_card(request: dict) -> None:
    number, details = request["request_id"], request["details"]
    icon, color = ACTION_STYLE.get(request["action"], (":material/help:", "gray"))
    with st.container(border=True, key=f"request_{number}"):
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            st.badge(request["action"], icon=icon, color=color)
            st.caption(f"#{number} · {ago(request['requested_time'])}")

        if details:
            lines = [f"**{details.get('customer', '')}**",
                     f":material/route: {details.get('route', '')}",
                     f":material/schedule: {details.get('pickup', '')}",
                     f":material/local_taxi: {details.get('taxi', '')}",
                     f":material/receipt_long: {details.get('note', '')}"]
            if details.get("pickup_at"):
                lines.append(f":orange-badge[:material/timer: {countdown(details['pickup_at'])}]")
            st.markdown(escape_dollars("  \n".join(lines)))
        else:
            st.markdown(escape_dollars(request["summary"]))

        # Once the pickup time has passed, the tool would refuse anyway, so the request can only be rejected.
        expired = bool(details.get("pickup_at")) and minutes_until(details["pickup_at"]) <= 0
        message = st.text_input("Message to the customer", key=f"message_{number}", label_visibility="collapsed",
                                placeholder="Message to the customer (needed to reject)")
        with st.container(horizontal=True):
            if st.button("Approve", key=f"approve_{number}", icon=":material/check:", type="primary", disabled=expired,
                         help="The pickup time has passed, so this request can only be rejected." if expired else None):
                with st.spinner("Sending the approval…"):
                    decide(request, True, message.strip())
                st.rerun()
            if st.button("Reject", key=f"reject_{number}", icon=":material/close:"):
                if message.strip():
                    with st.spinner("Sending the rejection…"):
                        decide(request, False, message.strip())
                    st.rerun()
                else:
                    st.warning("Add a message for the customer before rejecting.", icon=":material/edit_note:")


def representative_panel() -> None:
    with st.container(border=True, key="panel_representative"):
        waiting = db.list_approval_requests("waiting")
        panel_header("representative", ":material/fact_check:", "Approval requests",
                     "Bookings, changes and cancellations for trips less than 24 hours away wait here.",
                     (f"{len(waiting)} waiting", "orange") if waiting else ("All clear", "green"),
                     dict(icon=":material/refresh:", help="Refresh", key="refresh_requests"))
        if error := st.session_state.pop("representative_error", None):
            st.error(error, icon=":material/error:")

        with st.container(height=480, border=False, key="representative_body"):  # scrolls inside the panel
            if not waiting:
                st.success("**You're all caught up.** New short-notice requests appear here.",
                           icon=":material/task_alt:")
            for request in reversed(waiting):  # oldest first
                request_card(request)

            decided = [request for request in db.list_approval_requests() if request["status"] != "waiting"][:5]
            if decided:
                st.markdown("##### Recent decisions")
                for request in decided:
                    details = request["details"]
                    badge = (":green-badge[:material/check: Approved]" if request["status"] == "approved"
                             else ":red-badge[:material/close: Rejected]")
                    st.markdown(f"{badge} **{request['action']}** · {details.get('customer', '')}")
                    trip = " · ".join(part for part in (details.get("route"), details.get("pickup")) if part)
                    message = (f"  \n“{request['representative_message']}”" if request["representative_message"]
                               else "")
                    st.caption(escape_dollars(f"{trip}{message}"))


# --------- Company admin ---------

def with_status_labels(rows: list[dict]) -> list[dict]:
    return [{**row, "status": STATUS_LABEL.get(row["status"], row["status"])} for row in rows]


def table(rows: list[dict], empty: str, column_config: dict) -> None:
    """A tidy table showing only the columns named in column_config, in that order."""
    if rows:
        st.dataframe(with_status_labels(rows) if "status" in rows[0] else rows, hide_index=True,
                     use_container_width=True, column_config=column_config, column_order=list(column_config))
    else:
        st.caption(empty)


def admin_panel() -> None:
    with st.container(border=True, key="panel_admin"):
        bookings, customers = db.list_bookings(), db.list_customers()
        taxis, requests = db.list_taxi_catalog(), db.list_approval_requests()
        panel_header("admin", ":material/monitoring:", "Operations overview",
                     "Bookings, customers, taxis and approvals, as they happen.", ("Live", "blue"),
                     dict(icon=":material/refresh:", help="Refresh", key="refresh_admin"))
        body = st.container(height=480, border=False, key="admin_body")  # scrolls inside the panel

        with body.container(horizontal=True, key="row_metrics_1"):
            st.metric("Confirmed bookings", sum(b["status"] == "confirmed" for b in bookings), border=True)
            st.metric("Awaiting approval", sum(r["status"] == "waiting" for r in requests), border=True)
        with body.container(horizontal=True, key="row_metrics_2"):
            st.metric("Customers", len(customers), border=True)
            st.metric("All bookings", len(bookings), border=True)

        tabs = body.tabs([":material/directions_car: Bookings", ":material/group: Customers",
                          ":material/local_taxi: Taxis", ":material/fact_check: Approvals"])
        with tabs[0]:
            table(bookings, "No bookings yet.", {
                "confirmation_number": st.column_config.NumberColumn("No.", format="%d", width="small"),
                "customer_name": "Customer", "pickup_datetime": "Pickup", "status": "Status",
                "fare": st.column_config.NumberColumn("Fare", format="£%.2f"),
                "taxi_type": "Taxi", "passengers": st.column_config.NumberColumn("Passengers", format="%d"),
                "source": "From", "destination": "To",
                "distance_miles": st.column_config.NumberColumn("Miles", format="%.1f"),
                "customer_type": "Type", "booking_time": "Booked at",
            })
        with tabs[1]:
            table(customers, "No customers yet.", {
                "customer_name": "Name", "customer_type": "Type", "company_name": "Company",
                "customer_email": "Email", "customer_phone_number": "Phone",
                "company_reference_number": "Company ref",
                "customer_id": st.column_config.NumberColumn("ID", format="%d"),
            })
        with tabs[2]:
            table(taxis, "No taxi types yet.", {
                "taxi_type": "Taxi",
                "max_passengers": st.column_config.NumberColumn("Passengers", format="%d"),
                "business_rate_per_mile": st.column_config.NumberColumn("Business, per mile", format="£%.2f"),
                "personal_rate_per_mile": st.column_config.NumberColumn("Personal, per mile", format="£%.2f"),
            })
        with tabs[3]:
            table([{key: r[key] for key in ("request_id", "action", "summary", "status", "representative_message",
                                            "requested_time", "decided_time")} for r in requests],
                  "No approval requests yet.", {
                "request_id": st.column_config.NumberColumn("No.", format="%d", width="small"),
                "action": "Action", "status": "Status", "summary": "Request",
                "representative_message": "Message", "requested_time": "Requested", "decided_time": "Decided",
            })


# --------- Page ---------

def sidebar() -> None:
    """A short guide to the demo."""
    with st.sidebar:
        st.markdown("### :material/groups: Three personas")
        st.markdown("This demo shows what each person sees, side by side:\n"
                    "- **Customer** chats with the booking assistant.\n"
                    "- **Taxi company representative** approves or rejects short-notice requests.\n"
                    "- **Company admin** watches bookings, customers and taxis.")
        st.caption("In a real deployment, each persona would have its own page behind sign-in.")
        st.divider()
        st.markdown("### :material/verified: Human approval")
        st.markdown("Bookings, changes and cancellations **less than 24 hours** before pickup wait for a "
                    "representative. Everything else is confirmed straight away.")
        st.divider()
        st.markdown("### :material/science: Try it")
        st.markdown("Personal customer: **john.doe@example.com**  \nBusiness customer: company **C1**")
        st.markdown("Book a **Coupe** for 3 passengers to see the assistant refuse it and suggest a bigger taxi.")


def hero() -> None:
    waiting = len(db.list_approval_requests("waiting"))
    confirmed = sum(booking["status"] == "confirmed" for booking in db.list_bookings())
    st.html(f"""
        <div class="tba-hero">
          <div class="tba-brand">
            <div class="tba-logo">🚕</div>
            <div>
              <div class="tba-title">Taxi Booking Assistant</div>
              <div class="tba-subtitle">AI concierge for business and personal customers, with a human in the loop.</div>
            </div>
          </div>
          <div class="tba-chips">
            <span class="tba-chip"><span class="tba-dot"></span>Assistant online</span>
            <span class="tba-chip"><b>{waiting}</b> awaiting approval</span>
            <span class="tba-chip"><b>{confirmed}</b> confirmed bookings</span>
          </div>
        </div>""")


st.html(STYLE)
sidebar()
if flash := st.session_state.pop("flash", None):
    st.toast(flash[0], icon=flash[1])
hero()

chat_column, representative_column, admin_column = st.columns([1.1, 1, 1.15], gap="medium")
with chat_column:
    chat_panel()
with representative_column:
    representative_panel()
with admin_column:
    admin_panel()
