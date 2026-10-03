"""Which Miro endpoints hold what, and what each costs. Kept as data so new collections only need a table entry."""

from dataclasses import dataclass
from urllib.parse import quote

# Rate-limit levels (see client.LEVEL_COST). Listings are Level 2 per the API spec; downloading a
# file through a resource URL costs 500 credits (Level 3), measured against real boards.
DETAIL_LEVEL = 1
ITEMS_BY_TAG_LEVEL = 1
RESOURCE_LEVEL = 3

DOC_CONTENT_TYPES = ("markdown", "html")


def board_path(board_id):
	return quote(board_id, safe="=")


def experimental_item_path(board_id, item_id):
	"""The /items listing omits `data` for widgets it can't represent (flowchart shapes, mind map
	nodes, ...). For some of those types this endpoint returns the full payload."""
	return f"/v2-experimental/boards/{board_path(board_id)}/items/{quote(str(item_id), safe='')}"


def doc_content_path(board_id, item_id):
	return f"/v2/boards/{board_path(board_id)}/docs/{quote(str(item_id), safe='')}"


def needs_detail(item):
	"""True when the listing couldn't represent the item. The listing carries data and style for
	every supported type; per-type GET endpoints were verified to add nothing beyond it."""
	return not isinstance(item.get("data"), dict)


@dataclass(frozen=True)
class Collection:
	name: str               # output file / raw folder name
	path: str               # path template, {board} is replaced
	paging: str             # "cursor" or "offset"
	level: int = 1          # rate-limit level per page
	experimental: bool = False
	as_items: bool = False  # merge results into the item set (placed by frame like any item)
	default_type: str = ""  # type to assume when the payload has none

	def url(self, board_id):
		return self.path.format(board=board_path(board_id))


ITEMS = Collection("items", "/v2/boards/{board}/items", "cursor", level=2)

COLLECTIONS = (
	Collection("connectors", "/v2/boards/{board}/connectors", "cursor", level=2),
	Collection("groups", "/v2/boards/{board}/groups", "cursor", level=2),
	Collection("tags", "/v2/boards/{board}/tags", "offset"),
	Collection("members", "/v2/boards/{board}/members", "offset"),
	Collection("mindmap_nodes", "/v2-experimental/boards/{board}/mindmap_nodes", "cursor", level=2, experimental=True, as_items=True, default_type="mindmap_node"),
	Collection("code_widgets", "/v2-experimental/boards/{board}/code_widgets", "cursor", level=2, experimental=True, as_items=True, default_type="code_widget"),
)
