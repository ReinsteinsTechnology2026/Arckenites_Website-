from datetime import time

# Fixed daily lab slot template — the same four 2-hour blocks repeat every
# day. These are placeholder defaults so booking works end-to-end; swap them
# (and the capacity) once the real lab setup/schedule is provided.
LAB_SLOT_TEMPLATE: list[tuple[time, time]] = [
    (time(9, 0), time(11, 0)),
    (time(11, 0), time(13, 0)),
    (time(14, 0), time(16, 0)),
    (time(16, 0), time(18, 0)),
]

LAB_SLOT_CAPACITY = 10          # seats per slot per day
LAB_BOOKING_HORIZON_DAYS = 14   # how many days ahead students can book
LAB_SLOT_HOURS = 2              # fixed duration of every booking
LAB_WEEKLY_HOUR_CAP = 6         # max hours a student can book per week (Mon–Sun)
