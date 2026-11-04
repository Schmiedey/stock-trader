"""
NYSE Market Calendar and Trading Day Calculation Utilities.
Handles full US market holiday rules, day-counting for holding periods, and mandatory exit dates.
"""

import datetime
from typing import List, Optional


def easter_date(year: int) -> datetime.date:
    """Computes Western Easter date using the standard Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(year, month, day)


def get_nyse_holidays(year: int) -> List[datetime.date]:
    """Returns list of official NYSE market holidays for a given year."""
    holidays = []

    # 1. New Year's Day (observed)
    nyd = datetime.date(year, 1, 1)
    if nyd.weekday() == 5:  # Saturday -> Dec 31 prior year
        holidays.append(datetime.date(year - 1, 12, 31))
    elif nyd.weekday() == 6:  # Sunday -> Jan 2
        holidays.append(datetime.date(year, 1, 2))
    else:
        holidays.append(nyd)

    # 2. Martin Luther King Jr. Day: 3rd Monday in January
    jan1 = datetime.date(year, 1, 1)
    mlk_day = (0 - jan1.weekday() + 7) % 7 + 14 + 1
    holidays.append(datetime.date(year, 1, mlk_day))

    # 3. Washington's Birthday (Presidents' Day): 3rd Monday in February
    feb1 = datetime.date(year, 2, 1)
    pres_day = (0 - feb1.weekday() + 7) % 7 + 14 + 1
    holidays.append(datetime.date(year, 2, pres_day))

    # 4. Good Friday: Friday before Easter Sunday
    easter = easter_date(year)
    holidays.append(easter - datetime.timedelta(days=2))

    # 5. Memorial Day: Last Monday in May
    may31 = datetime.date(year, 5, 31)
    memorial = may31 - datetime.timedelta(days=(may31.weekday() - 0) % 7)
    holidays.append(memorial)

    # 6. Juneteenth National Independence Day (June 19, observed)
    june19 = datetime.date(year, 6, 19)
    if june19.weekday() == 5:
        holidays.append(datetime.date(year, 6, 18))
    elif june19.weekday() == 6:
        holidays.append(datetime.date(year, 6, 20))
    else:
        holidays.append(june19)

    # 7. Independence Day (July 4, observed)
    july4 = datetime.date(year, 7, 4)
    if july4.weekday() == 5:
        holidays.append(datetime.date(year, 7, 3))
    elif july4.weekday() == 6:
        holidays.append(datetime.date(year, 7, 5))
    else:
        holidays.append(july4)

    # 8. Labor Day: 1st Monday in September
    sep1 = datetime.date(year, 9, 1)
    labor_day = (0 - sep1.weekday() + 7) % 7 + 1
    holidays.append(datetime.date(year, 9, labor_day))

    # 9. Thanksgiving Day: 4th Thursday in November
    nov1 = datetime.date(year, 11, 1)
    thursdays = [(nov1 + datetime.timedelta(days=x)) for x in range(28) if (nov1 + datetime.timedelta(days=x)).weekday() == 3]
    holidays.append(thursdays[3])

    # 10. Christmas Day (December 25, observed)
    xmas = datetime.date(year, 12, 25)
    if xmas.weekday() == 5:
        holidays.append(datetime.date(year, 12, 24))
    elif xmas.weekday() == 6:
        holidays.append(datetime.date(year, 12, 26))
    else:
        holidays.append(xmas)

    return holidays


def is_trading_day(d: datetime.date) -> bool:
    """Returns True if the date is a regular NYSE trading session."""
    if d.weekday() >= 5:  # Saturday or Sunday
        return False
    holidays = get_nyse_holidays(d.year)
    # Check if late Dec of prior year observed New Year's
    if d.month == 12 and d.day == 31:
        next_year_holidays = get_nyse_holidays(d.year + 1)
        if d in next_year_holidays:
            return False
    return d not in holidays


def get_next_trading_day(d: datetime.date) -> datetime.date:
    """Gets the next trading day after date d."""
    cur = d + datetime.timedelta(days=1)
    while not is_trading_day(cur):
        cur += datetime.timedelta(days=1)
    return cur


def get_previous_trading_day(d: datetime.date) -> datetime.date:
    """Gets the closest previous trading day before date d."""
    cur = d - datetime.timedelta(days=1)
    while not is_trading_day(cur):
        cur -= datetime.timedelta(days=1)
    return cur


def count_trading_days_held(buy_date: datetime.date, as_of_date: datetime.date) -> int:
    """
    Counts full trading sessions elapsed since buy_date.
    According to the Spinach SOP:
    - Bought Monday Close -> Day 1 is Tuesday, Day 2 is Wednesday, ..., Day 5 is Monday.
    - If today is buy_date itself, days held = 0.
    """
    if as_of_date <= buy_date:
        return 0
    
    count = 0
    cur = buy_date + datetime.timedelta(days=1)
    while cur <= as_of_date:
        if is_trading_day(cur):
            count += 1
        cur += datetime.timedelta(days=1)
    return count


def get_mandatory_exit_date(buy_date: datetime.date, max_holding_days: int = 5) -> datetime.date:
    """
    Computes the exact calendar date of Day 5 close (or max_holding_days close).
    """
    cur = buy_date + datetime.timedelta(days=1)
    count = 0
    while count < max_holding_days:
        if is_trading_day(cur):
            count += 1
            if count == max_holding_days:
                return cur
        cur += datetime.timedelta(days=1)
    return cur
