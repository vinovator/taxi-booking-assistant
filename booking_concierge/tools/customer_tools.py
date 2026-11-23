from google.adk.tools import ToolContext

from booking_concierge.data import database as db


def _save_to_state(tool_context: ToolContext, customer_type: str, customer: dict | None = None,
                   company_reference_number: str | None = None, company_name: str | None = None) -> None:
    """Save who the caller is in session state, where the booking agents read it."""
    customer = customer or {}
    tool_context.state["customer_type"] = customer_type
    tool_context.state["company_reference_number"] = company_reference_number
    tool_context.state["company_name"] = company_name
    tool_context.state["customer_id"] = customer.get("customer_id")
    tool_context.state["customer_name"] = customer.get("customer_name")
    tool_context.state["email"] = customer.get("customer_email")
    tool_context.state["phone_number"] = customer.get("customer_phone_number")


def _upcoming_bookings(customer: dict) -> str:
    """How many upcoming bookings the customer has. booking_concierge only asks "new booking or manage an
    existing one?" when there's something to manage."""
    return f"Upcoming bookings: {db.count_upcoming_bookings(customer['customer_id'])}."


# Lookup company details tool implementation
def lookup_company_details(company_reference_number: str, tool_context: ToolContext) -> str:
    """Lookup company details using company reference number

    Args:
        company_reference_number: Company reference number to lookup

    Returns:
        Company name if found, otherwise "Company not found"
    """
    # Reference numbers are stored in upper case, so "c1" finds C1.
    company_reference_number = str(company_reference_number).strip().upper()
    company_name = db.find_business_customer(company_reference_number)
    if company_name is None:
        return 'Company not found'

    # The passenger isn't known yet; add_business_customer_details fills that in.
    _save_to_state(tool_context, "business", company_reference_number=company_reference_number,
                   company_name=company_name)
    return company_name


def add_business_customer_details(name: str, email: str, phone_number: str, tool_context: ToolContext) -> str:
    """Add the passenger's details for the company found with lookup_company_details

    If the passenger has travelled for this company before, their saved details are used.

    Args:
        name: Name of the passenger
        email: Email of the passenger
        phone_number: Phone number of the passenger

    Returns:
        Confirmation message, with how many upcoming bookings the passenger has
    """
    company_reference_number = tool_context.state.get("company_reference_number")
    if not company_reference_number:
        return "Look up the company reference number first."

    customer = db.add_customer("business", name, email, phone_number, company_reference_number)
    if customer["company_reference_number"] != company_reference_number:
        return "This email is already registered to a different account. Please use a different email."

    _save_to_state(tool_context, "business", customer, company_reference_number,
                   tool_context.state.get("company_name"))
    return (f"Business customer details added: {customer['customer_name']}, "
            f"{customer['customer_email']}, {customer['customer_phone_number']}. {_upcoming_bookings(customer)}")


def lookup_personal_customer_details(email: str, tool_context: ToolContext) -> str:
    """Lookup personal customer details using email

    Args:
        email: Email of the personal customer to lookup

    Returns:
        The customer's name, email, phone number and how many upcoming bookings they have if found,
        otherwise "Personal customer not found"
    """
    customer = db.find_personal_customer(email)
    if customer is None:
        return 'Personal customer not found'

    _save_to_state(tool_context, "personal", customer)
    return (f"Name: {customer['customer_name']}, Email: {customer['customer_email']}, "
            f"Phone number: {customer['customer_phone_number']}. {_upcoming_bookings(customer)}")


def add_personal_customer_details(name: str, email: str, phone_number: str, tool_context: ToolContext) -> str:
    """Add personal customer details to the data store

    Args:
        name: Name of the personal customer
        email: Email of the personal customer
        phone_number: Phone number of the personal customer

    Returns:
        Confirmation message, with how many upcoming bookings the customer has
    """
    customer = db.add_customer("personal", name, email, phone_number)
    if customer["customer_type"] != "personal":
        return "This email is already registered to a business account. Please use a different email."

    _save_to_state(tool_context, "personal", customer)
    return (f"Personal customer details added: {customer['customer_name']}, "
            f"{customer['customer_email']}, {customer['customer_phone_number']}. {_upcoming_bookings(customer)}")
