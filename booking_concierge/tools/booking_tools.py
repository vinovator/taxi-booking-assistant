from datetime import datetime, timedelta

from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import ToolContext

from booking_concierge.data import database as db

# Pickups are checked and saved in this format, e.g. "2026-11-24 14:30".
PICKUP_FORMAT = "%Y-%m-%d %H:%M"

# Returned by every tool when session state doesn't say who the customer is.
NOT_IDENTIFIED = ("The customer hasn't been identified yet. Transfer back to booking_concierge "
                  "so it can fetch the customer's details.")


def remember_current_time(callback_context: CallbackContext) -> None:
    """Save the current date and time in session state, where the sub-agents' prompts show it.

    booking_agent and manage_booking_agent run this before every turn (their before_agent_callback),
    so "tomorrow" or "in two hours" is worked out from the real time, even in a conversation that
    started days ago.
    """
    callback_context.state["current_time"] = datetime.now().strftime("%A %Y-%m-%d %H:%M")


def _pickup_datetime(pickup_date: str, pickup_time: str) -> datetime:
    """Turn a date like 2026-11-24 and a time like 14:30 into a pickup in the future.

    Raises ValueError with a message for the customer if the date or time is wrong.
    """
    try:
        pickup = datetime.strptime(f"{str(pickup_date).strip()} {str(pickup_time).strip()}", PICKUP_FORMAT)
    except ValueError:
        raise ValueError(f"I couldn't read the pickup date {pickup_date!r} and time {pickup_time!r}. "
                         "Please give the date as YYYY-MM-DD and the time as HH:MM on a 24-hour clock, "
                         "for example 2026-11-24 and 14:30.") from None

    now = datetime.now()
    if pickup <= now:
        raise ValueError(f"The pickup time {pickup:%Y-%m-%d %H:%M} has already passed. "
                         f"It is now {now:%A %Y-%m-%d %H:%M}, so please choose a later time.")
    return pickup


def _cannot_change(booking: dict | None, confirmation_number: int) -> str | None:
    """Explain why a booking can't be modified or cancelled, or return None if it can."""
    if booking is None:
        return f"No booking found with confirmation number {confirmation_number}."
    if booking["status"] != "confirmed":
        return f"Booking {confirmation_number} is {booking['status']}, so it can't be changed."
    if datetime.strptime(booking["pickup_datetime"], PICKUP_FORMAT) <= datetime.now():
        return (f"The pickup time for booking {confirmation_number} ({booking['pickup_datetime']}) "
                "has already passed, so it can't be changed.")
    return None


def _within_24_hours(pickup: datetime) -> bool:
    """Trips less than 24 hours away need a representative's approval to book, change or cancel."""
    return pickup - datetime.now() < timedelta(hours=24)


def _rejection(tool_context: ToolContext) -> str | None:
    """The representative's message for the customer if they rejected this tool call, otherwise None.

    Each tool checks this first. A rejected request changes nothing, so the message is passed on even if
    the pickup has passed, or the booking has changed, while the request was waiting.
    """
    decision = tool_context.tool_confirmation
    if decision is None or decision.confirmed:
        return None
    message = (decision.payload or {}).get("message") or "No reason given."
    return f"Not approved. Message from our team: {message}"


def _approval(tool_context: ToolContext, request: dict) -> str | None:
    """Ask a representative to approve this tool call, using ADK's tool confirmation.

    `request` describes the call for the representative's panel: action, customer, route, pickup,
    pickup_at (the pickup that falls within 24 hours), taxi and note.
    The first time, ADK pauses the call and the representative sees the request. When they decide,
    ADK runs the same tool call again with the decision attached.
    Returns the message for the customer while the request waits, or None once it's approved.
    """
    if tool_context.tool_confirmation is None:
        summary = f"{request['action']} for {request['customer']}: {request['route']}, {request['pickup']}"
        tool_context.request_confirmation(hint=summary, payload=request)
        return "Sent to our team for approval. They'll reply here shortly."
    return None  # approved (a rejection was already handled by _rejection)


def _customer_label(state) -> str:
    """The customer's name, with their company for business customers."""
    company = state.get("company_name")
    return f"{state.get('customer_name')} ({company})" if company else str(state.get("customer_name"))


def _when(pickup: datetime) -> str:
    """A pickup time for people to read, e.g. "Mon 23 Nov, 14:30"."""
    return f"{pickup:%a %d %b, %H:%M}"


def _taxi_label(taxi_type: str, passengers: int) -> str:
    """A taxi and its passengers for people to read, e.g. "SUV · 5 passengers"."""
    return f"{taxi_type} · {passengers} passenger{'' if passengers == 1 else 's'}"


def _change(before: str, after: str) -> str:
    """Show a change as "before → after", or just the value if it didn't change."""
    return after if before == after else f"{before} → {after}"


def _no_such_taxi(taxi_type: str, customer_type: str) -> str:
    """The message for a taxi type that isn't in the catalog, naming the ones that are."""
    names = ", ".join(option["taxi_type"] for option in db.list_taxi_types(customer_type))
    return f"We don't have a taxi type called {taxi_type!r}. Choose one of: {names}."


def _taxi_problem(taxi: dict | None, taxi_type: str, passengers: int, customer_type: str) -> str | None:
    """Explain why this taxi won't do for this many passengers, or return None if it will.

    `taxi` is what db.get_taxi_type found for `taxi_type` (None if there's no such taxi type).
    If the taxi is too small, suggest the cheapest one that carries everyone.
    """
    if taxi is None:
        return _no_such_taxi(taxi_type, customer_type)
    if passengers < 1:
        return "The number of passengers must be at least 1."
    if passengers <= taxi["max_passengers"]:
        return None

    too_small = (f"A {taxi['taxi_type']} carries up to {taxi['max_passengers']} passengers, "
                 f"so it can't take {passengers}.")
    fits = [option for option in db.list_taxi_types(customer_type) if option["max_passengers"] >= passengers]
    if not fits:
        return f"{too_small} None of our taxis carries {passengers} passengers, so suggest booking two taxis."
    best = fits[0]  # list_taxi_types is cheapest first
    return (f"{too_small} Suggest the {best['taxi_type']} instead: it carries up to {best['max_passengers']} "
            f"passengers, at ${best['rate_per_mile']:.2f} a mile.")


def list_taxi_types(tool_context: ToolContext) -> list[dict] | str:
    """
    List the taxi types the customer can choose from: how many passengers each one carries, and its price per
    mile for this customer. The web chat shows them to the customer as cards with pictures.

    Returns:
        list: One entry per taxi type, cheapest first, with taxi_type, max_passengers and rate_per_mile,
        or a message if the customer hasn't been identified.
    """
    customer_type = tool_context.state.get("customer_type")
    if customer_type is None:
        return NOT_IDENTIFIED
    return db.list_taxi_types(customer_type)


def calculate_total_fare(distance: float, taxi_type: str, tool_context: ToolContext) -> float | str:
    """
    Calculate the total fare: the distance times this customer's price per mile for the taxi type.

    Args:
        distance (float): The distance between source and destination in miles.
        taxi_type (str): The taxi type the customer chose, e.g. Sedan (one of the types from list_taxi_types)

    Returns:
        float: The total fare, or a message if it can't be calculated.
    """
    customer_type = tool_context.state.get("customer_type")
    if customer_type is None:
        return NOT_IDENTIFIED
    if distance <= 0:
        return "The distance must be more than 0 miles."

    taxi = db.get_taxi_type(taxi_type, customer_type)
    if taxi is None:
        return _no_such_taxi(taxi_type, customer_type)
    return round(distance * taxi["rate_per_mile"], 2)


def confirm_booking(source: str, destination: str, distance: float, pickup_date: str, pickup_time: str,
                    taxi_type: str, passengers: int, tool_context: ToolContext) -> str:
    """
    Confirm the booking for the current customer, save it, and return a confirmation message.
    Pickups less than 24 hours away are sent to a representative for approval first.
    If the taxi is too small for the passengers, nothing is booked and a taxi that fits is suggested.

    Args:
        source (str): Source location
        destination (str): Destination location
        distance (float): Distance between source and destination in miles, from calculate_distance
        pickup_date (str): Date of pickup as YYYY-MM-DD, e.g. 2026-11-24
        pickup_time (str): Time of pickup as HH:MM on a 24-hour clock, e.g. 14:30
        taxi_type (str): The taxi type the customer chose, e.g. Sedan (one of the types from list_taxi_types)
        passengers (int): How many passengers are travelling

    Returns:
        str: Confirmation message with booking details, what needs to be corrected, or the approval status.
    """
    rejection = _rejection(tool_context)
    if rejection:
        return rejection

    state = tool_context.state
    customer_id = state.get("customer_id")
    if customer_id is None:
        return NOT_IDENTIFIED
    if distance <= 0:
        return "The distance must be more than 0 miles. Use calculate_distance to get it."

    try:
        pickup = _pickup_datetime(pickup_date, pickup_time)
    except ValueError as error:
        return str(error)

    # Everyone must fit in the taxi. Checked before asking for approval, so representatives only see requests
    # that can go ahead.
    taxi = db.get_taxi_type(taxi_type, state.get("customer_type"))
    problem = _taxi_problem(taxi, taxi_type, passengers, state.get("customer_type"))
    if problem:
        return problem

    # Recalculate instead of trusting a fare from the chat; the price comes from the taxi type and the
    # customer type in session state.
    fare = calculate_total_fare(distance, taxi["taxi_type"], tool_context)

    if _within_24_hours(pickup):
        waiting = _approval(tool_context, {
            "action": "New booking",
            "customer": _customer_label(state),
            "route": f"{source} → {destination}",
            "pickup": _when(pickup),
            "pickup_at": pickup.strftime(PICKUP_FORMAT),
            "taxi": _taxi_label(taxi["taxi_type"], passengers),
            "note": f"{distance:.1f} miles · ${fare:.2f}",
        })
        if waiting:
            return waiting

    # The database assigns the confirmation number when the booking is saved. The taxi type is saved with the
    # catalog's spelling ("suv" becomes "SUV").
    confirmation_number = db.create_booking(customer_id, source, destination, pickup.strftime(PICKUP_FORMAT),
                                            taxi["taxi_type"], passengers, distance, fare)

    confirmation_message = (
        f"Booking confirmed for {_customer_label(state)}!\n"
        f"From: {source}\n"
        f"To: {destination}\n"
        f"Taxi: {_taxi_label(taxi['taxi_type'], passengers)}\n"
        f"Distance: {distance:.2f} miles\n"
        f"Pickup Date: {pickup:%A %Y-%m-%d}\n"
        f"Pickup Time: {pickup:%H:%M}\n"
        f"Total Fare: ${fare:.2f}\n"
        f"Confirmation Number: {confirmation_number}"
    )
    return confirmation_message


def lookup_booking_details(confirmation_number: int, tool_context: ToolContext) -> dict:
    """
    Lookup booking details based on the confirmation number.

    Args:
        confirmation_number (int): The booking confirmation number

    Returns:
        dict: The booking details, or a message if no booking was found.
    """
    customer_id = tool_context.state.get("customer_id")
    if customer_id is None:
        return {"message": NOT_IDENTIFIED}

    booking = db.get_booking(confirmation_number, customer_id)
    if booking is None:
        return {"message": f"No booking found with confirmation number {confirmation_number}."}
    booking.pop("customer_id")
    return booking


def modify_booking(confirmation_number: int, tool_context: ToolContext,
                   new_pickup_date: str | None = None, new_pickup_time: str | None = None,
                   new_taxi_type: str | None = None, new_passengers: int | None = None) -> str:
    """
    Change a booking's pickup date and/or time, its taxi type, or its number of passengers.
    Give only what's changing; the rest stays as it is. The fare is worked out again for the taxi type.
    If the current or the new pickup is less than 24 hours away, a representative must approve it first.
    If the taxi would be too small for the passengers, nothing changes and a taxi that fits is suggested.

    Args:
        confirmation_number (int): The booking confirmation number
        new_pickup_date (str, optional): New pickup date as YYYY-MM-DD, e.g. 2026-11-25
        new_pickup_time (str, optional): New pickup time as HH:MM on a 24-hour clock, e.g. 14:30
        new_taxi_type (str, optional): New taxi type, e.g. SUV (one of the types from list_taxi_types)
        new_passengers (int, optional): New number of passengers

    Returns:
        str: A message indicating the result of the modification, or the approval status.
    """
    rejection = _rejection(tool_context)
    if rejection:
        return rejection

    state = tool_context.state
    customer_id = state.get("customer_id")
    if customer_id is None:
        return NOT_IDENTIFIED

    booking = db.get_booking(confirmation_number, customer_id)
    problem = _cannot_change(booking, confirmation_number)
    if problem:
        return problem
    if not (new_pickup_date or new_pickup_time or new_taxi_type or new_passengers):
        return "Please say what to change: the pickup date or time, the taxi type, or the number of passengers."

    # Keep whatever isn't changing from the booking.
    current = datetime.strptime(booking["pickup_datetime"], PICKUP_FORMAT)
    try:
        pickup = _pickup_datetime(new_pickup_date or f"{current:%Y-%m-%d}", new_pickup_time or f"{current:%H:%M}")
    except ValueError as error:
        return str(error)

    taxi_type, passengers = new_taxi_type or booking["taxi_type"], new_passengers or booking["passengers"]
    taxi = db.get_taxi_type(taxi_type, state.get("customer_type"))
    problem = _taxi_problem(taxi, taxi_type, passengers, state.get("customer_type"))
    if problem:
        return problem
    fare = calculate_total_fare(booking["distance_miles"], taxi["taxi_type"], tool_context)

    if _within_24_hours(current) or _within_24_hours(pickup):
        waiting = _approval(tool_context, {
            "action": "Change",
            "customer": _customer_label(state),
            "route": f"{booking['source']} → {booking['destination']}",
            "pickup": _change(_when(current), _when(pickup)),
            "pickup_at": min(current, pickup).strftime(PICKUP_FORMAT),
            "taxi": _change(_taxi_label(booking["taxi_type"], booking["passengers"]),
                            _taxi_label(taxi["taxi_type"], passengers)),
            "note": f"Booking {confirmation_number} · " + _change(f"${booking['fare']:.2f}", f"${fare:.2f}"),
        })
        if waiting:
            return waiting

    db.change_booking(confirmation_number, customer_id, pickup.strftime(PICKUP_FORMAT),
                      taxi["taxi_type"], passengers, fare)
    return (f"Booking {confirmation_number} has been changed. Pickup: {pickup:%A %Y-%m-%d %H:%M}. "
            f"Taxi: {_taxi_label(taxi['taxi_type'], passengers)}. Fare: ${fare:.2f}.")


def cancel_booking(confirmation_number: int, tool_context: ToolContext) -> str:
    """
    Cancel the booking based on the confirmation number.
    If the pickup is less than 24 hours away, a representative must approve it first.

    Args:
        confirmation_number (int): The booking confirmation number

    Returns:
        str: A message indicating the result of the cancellation, or the approval status.
    """
    rejection = _rejection(tool_context)
    if rejection:
        return rejection

    state = tool_context.state
    customer_id = state.get("customer_id")
    if customer_id is None:
        return NOT_IDENTIFIED

    booking = db.get_booking(confirmation_number, customer_id)
    problem = _cannot_change(booking, confirmation_number)
    if problem:
        return problem

    pickup = datetime.strptime(booking["pickup_datetime"], PICKUP_FORMAT)
    if _within_24_hours(pickup):
        waiting = _approval(tool_context, {
            "action": "Cancellation",
            "customer": _customer_label(state),
            "route": f"{booking['source']} → {booking['destination']}",
            "pickup": _when(pickup),
            "pickup_at": pickup.strftime(PICKUP_FORMAT),
            "taxi": _taxi_label(booking["taxi_type"], booking["passengers"]),
            "note": f"Booking {confirmation_number} · ${booking['fare']:.2f}",
        })
        if waiting:
            return waiting

    db.cancel_booking(confirmation_number, customer_id)
    return f"Booking {confirmation_number} has been cancelled successfully."
