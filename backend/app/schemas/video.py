from pydantic import BaseModel


class VideoRoomOut(BaseModel):
    room_name: str
    display_name: str
    subject: str
