-- Starting data
-- Rows are only added if they are missing; so this is safe to run on every start.
-- Give each customer a fixed customer_id so it's never added twice.
-- Write company reference numbers in upper case; lookups convert what the customer types to upper case.
-- Taxi types can be looked up in any case (see schema.sql); bookings save the spelling used here.

INSERT OR IGNORE INTO companies (company_reference_number, company_name) VALUES
    ('C1', 'Acme Corporation'),
    ('C2', 'Globex Inc.'),
    ('C3', 'Initech LLC');


INSERT OR IGNORE INTO customers 
    (customer_id, customer_type, company_reference_number, customer_name, customer_email, customer_phone_number) VALUES
    (1, 'business', 'C1', 'Alice Smith', 'alice.smith@example.com', '555-0101'),
    (2, 'personal', NULL, 'John Doe', 'john.doe@example.com', '555-0102');


-- The taxi catalog: how many passengers each taxi type carries.
INSERT OR IGNORE INTO taxi_types (taxi_type, max_passengers) VALUES
    ('Coupe', 2),
    ('Sedan', 4),
    ('Executive', 4),
    ('SUV', 6),
    ('Van', 8);


-- The price per mile for each taxi type and customer type. The Sedan keeps the prices used before the catalog.
INSERT OR IGNORE INTO rates (taxi_type, customer_type, rate_per_mile) VALUES
    ('Coupe', 'business', 2.2),     ('Coupe', 'personal', 2.3),
    ('Sedan', 'business', 2.4),     ('Sedan', 'personal', 2.5),
    ('Executive', 'business', 4.5), ('Executive', 'personal', 4.8),
    ('SUV', 'business', 3.0),       ('SUV', 'personal', 3.2),
    ('Van', 'business', 3.4),       ('Van', 'personal', 3.6);


