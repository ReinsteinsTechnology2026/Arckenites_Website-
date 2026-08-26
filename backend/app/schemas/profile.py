from pydantic import BaseModel


class PublicProfileOut(BaseModel):
    """The only shape another student/trainer is ever allowed to see of
    someone else — photo + name, nothing else. Never add a field here;
    build a role-specific *AdminOut/MeResponse instead for anything more."""
    id: int
    full_name: str
    photo_url: str | None = None
