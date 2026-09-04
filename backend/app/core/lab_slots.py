from datetime import time

# Fixed daily lab slot template — twelve back-to-back 2-hour blocks covering
# the full 24-hour day (00:00 start through the 22:00 block). The last block
# ends at 23:59 rather than 24:00 since a slot can't cross into the next
# calendar date here. Placeholder capacity/coverage — swap once the real lab
# setup/schedule is provided.
LAB_SLOT_TEMPLATE: list[tuple[time, time]] = [
    (time(start_hour, 0), time(23, 59) if start_hour + 2 >= 24 else time(start_hour + 2, 0))
    for start_hour in range(0, 24, 2)
]

LAB_SLOT_CAPACITY = 10          # seats per slot per day
LAB_BOOKING_HORIZON_DAYS = 14   # how many days ahead students can book
LAB_SLOT_HOURS = 2              # fixed duration of every booking
LAB_WEEKLY_HOUR_CAP = 6         # max hours a student can book per week (Mon–Sun)
