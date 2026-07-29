from sqlmodel import select

from app.config import settings
from app.corpus import ingest_inspiration_posts
from app.extraction import Cohort, propose_hooks
from app.models.post import Post, PostSource

SLUG = "linkedin-archive"

# Shaped like `.scratch/corpus-widening/creator-inspiration.json`: a null metric means the
# document never recorded it, which is not the same as zero.
POSTS = [
    {
        "index": 1,
        "content": "I stopped chasing reach and started counting replies.",
        "likes": 4,
        "comments": 4,
        "shares": 4,
        "image_files": ["post-01_img-1_image2.png"],
        "image_url": None,
    },
    {
        "index": 2,
        "content": "Nobody tells you the first year is mostly deleting things.",
        "likes": 345,
        "comments": 49,
        "shares": 6,
        "image_files": [],
        "image_url": None,
    },
    {
        "index": 4,
        "content": "My dog made a mess of the demo and it went better than planned.",
        "likes": 18,
        "comments": 2,
        "shares": None,
        "image_files": [],
        "image_url": "https://media.licdn.com/dms/image/v2/expired",
    },
]


def ingest(session, images_dir=None, media_dir=None, posts=POSTS):
    return ingest_inspiration_posts(
        session, posts, doc_slug=SLUG, images_dir=images_dir, media_dir=media_dir
    )


def by_index(session, index: int) -> Post:
    return session.exec(
        select(Post).where(Post.zernio_id == f"inspiration:{SLUG}:{index}")
    ).one()


def png(directory, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(b"\x89PNG-creator-image")


def test_every_post_in_the_document_reaches_the_corpus(session):
    result = ingest(session)

    assert result.created == 3
    assert len(session.exec(select(Post)).all()) == 3


def test_creator_posts_are_recorded_as_someone_elses_writing(session):
    ingest(session)

    post = by_index(session, 1)
    assert post.source == PostSource.MANUAL
    assert post.platform == "linkedin"
    assert post.is_external is True


def test_the_cohort_is_named_by_settings_not_by_hand(session):
    """A username that does not match the setting drops the whole cohort from extraction."""
    ingest(session)

    assert by_index(session, 1).account_username == settings.inspiration_account


def test_each_metric_keeps_its_own_value(session):
    """Not the `likes = engaged_actions` shortcut `add_manual_post` takes."""
    ingest(session)

    post = by_index(session, 2)
    assert (post.likes, post.comments, post.shares) == (345, 49, 6)
    assert post.engaged_actions == 400


def test_a_metric_the_document_never_recorded_counts_as_zero(session):
    """The column cannot hold "unrecorded", so that post's engaged actions is a floor."""
    ingest(session)

    post = by_index(session, 4)
    assert post.shares == 0
    assert post.engaged_actions == 20


def test_a_creator_post_carries_no_publication_date(session):
    """The document does not record one, and inventing one would be evidence we do not have."""
    ingest(session)

    assert by_index(session, 1).published_at is None


def test_ids_are_namespaced_so_they_cannot_collide_with_zernios(session):
    ingest(session)

    assert by_index(session, 1).zernio_id == f"inspiration:{SLUG}:1"


def test_running_the_ingest_again_creates_nothing(session):
    ingest(session)

    again = ingest(session)

    assert (again.created, again.skipped) == (0, 3)
    assert len(session.exec(select(Post)).all()) == 3


def test_a_rerun_does_not_disturb_the_rows_already_there(session):
    ingest(session)
    by_index(session, 2).excluded_from_extraction = True
    session.flush()

    ingest(session)

    assert by_index(session, 2).excluded_from_extraction is True


def test_an_extracted_image_is_copied_into_the_served_media_directory(session, tmp_path):
    """Copied from disk, never re-fetched: the document's LinkedIn URLs have expired."""
    images, media = tmp_path / "extracted", tmp_path / "media"
    png(images, "post-01_img-1_image2.png")

    ingest(session, images_dir=images, media_dir=media)

    post = by_index(session, 1)
    assert post.local_media_path == f"inspiration_{SLUG}_1.png"
    assert post.media_type == "image"
    assert (media / post.local_media_path).read_bytes() == b"\x89PNG-creator-image"


def test_a_post_with_no_image_stores_no_media(session, tmp_path):
    ingest(session, images_dir=tmp_path, media_dir=tmp_path / "media")

    post = by_index(session, 2)
    assert post.local_media_path is None
    assert post.media_type is None


def test_an_image_the_extraction_never_produced_does_not_cost_us_the_post(session, tmp_path):
    """Media is supporting material — a missing file must never drop the writing."""
    ingest(session, images_dir=tmp_path / "empty", media_dir=tmp_path / "media")

    assert by_index(session, 1).local_media_path is None
    assert by_index(session, 1).content.startswith("I stopped chasing reach")


def test_the_expiring_cdn_url_is_not_stored_as_something_to_fetch(session):
    ingest(session)

    assert by_index(session, 4).media_items == []


class FakeLLM:
    def __init__(self):
        self.user = ""

    def complete_json(self, system: str, user: str) -> dict:
        self.user = user
        return {"hooks": [{"name": "n", "pattern": "{a}"}]}


def test_an_ingested_creator_post_reaches_inspiration_extraction(session):
    """The join the whole ingest exists for: undated rows under the inspiration account
    must survive `_strongest_posts`, or all of them are silently invisible."""
    ingest(session)
    llm = FakeLLM()

    propose_hooks(session, llm, cohort=Cohort.INSPIRATION)

    assert "Nobody tells you the first year" in llm.user


def test_a_creator_post_is_never_read_as_the_voice(session):
    ingest(session)
    llm = FakeLLM()

    assert propose_hooks(session, llm, cohort=Cohort.VOICE) == []
    assert llm.user == ""
