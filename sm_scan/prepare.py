"""落库前统一加工(P1-1):三条路径(主题/订阅/回填)共用,防再漏第四处。"""

from . import tags


def prepare_paper(
    p: dict,
    *,
    group_names: list[str] | None,
    g3_phrases: list[str],
    started: str,
    subscription: str | None = None,
) -> dict:
    """统一补齐:matched_groups(组 id 串)、tags、subscription、first_seen。

    group_names=None 保留论文已有命中(bioRxiv 本地过滤已给出);
    [] 显式清空(订阅轨);列表为命中组(主题轨)。
    stale(_stale 键)透传,由 store.upsert_paper 落到 stale 列。
    """
    if group_names is None:
        p.setdefault("matched_groups", [])
    else:
        p["matched_groups"] = list(group_names)
    p["tags"] = tags.assign(p.get("title", ""), p.get("abstract"), g3_phrases)
    if subscription is not None and not p.get("subscription"):
        p["subscription"] = subscription
    p["first_seen"] = started
    return p
