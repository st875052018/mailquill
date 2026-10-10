"""Gmail API 服務函式。純解析委派給 gmail_message。"""
from __future__ import annotations

import socket
import time

from mailquill.gmail_message import (
    Attachment, EmailMessage, decode_b64url, header_value, walk_payload,
)
from mailquill.google_auth import build_google_service

# 暫時性錯誤：逾時/連線中斷/伺服器忙碌 → 退避重試（避免單封逾時就讓整批 run 中斷）
_RETRYABLE = (TimeoutError, socket.timeout, ConnectionError, OSError)
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _execute(request, retries: int = 4, sleep=time.sleep):
    """執行 Gmail API 請求；遇暫時性錯誤以指數退避重試，重試用盡才拋出。"""
    from googleapiclient.errors import HttpError

    for attempt in range(retries):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status in _RETRY_STATUSES and attempt < retries - 1:
                sleep(min(2 ** attempt, 8))
                continue
            raise
        except _RETRYABLE:
            if attempt < retries - 1:
                sleep(min(2 ** attempt, 8))
                continue
            raise


def build_service(credentials_path: str, token_path: str):
    """OAuth 授權並回傳 Gmail service。整合用，不寫單元測試。"""
    return build_google_service("gmail", "v1", credentials_path, token_path)


def label_id(service, label_name: str, user_id: str = "me") -> str:
    resp = _execute(service.users().labels().list(userId=user_id))
    for lab in resp.get("labels", []):
        if lab.get("name") == label_name:
            return lab["id"]
    raise ValueError(f"找不到 Gmail label: {label_name}")


def list_label_messages(service, label_name: str, query: str | None = None,
                        user_id: str = "me") -> list[str]:
    lid = label_id(service, label_name, user_id)
    return _list_messages_for_label_id(service, lid, query, user_id)


def _list_messages_for_label_id(service, lid: str, query: str | None,
                                user_id: str) -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        resp = _execute(service.users().messages().list(
            userId=user_id, labelIds=[lid], q=query, pageToken=page_token,
        ))
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def gmail_after_query(since: str | None) -> str | None:
    """把 'YYYY-MM-DD' 轉成 Gmail 查詢 'after:YYYY/MM/DD'；since 為 None/空字串回 None。"""
    if not since:
        return None
    return "after:" + since.replace("-", "/")


def list_all_labels(service, user_id: str = "me") -> list[str]:
    """回傳 Gmail 內所有 Label 的名稱（排序）。除錯／設定用。"""
    resp = _execute(service.users().labels().list(userId=user_id))
    return sorted(lab.get("name", "") for lab in resp.get("labels", []))


def resolve_label_ids(service, label_names: list[str],
                      user_id: str = "me") -> tuple[list[str], list[str]]:
    """一次列出所有 label，回傳 (找到的 label id, 找不到的 label 名稱)。不拋例外。"""
    resp = _execute(service.users().labels().list(userId=user_id))
    name_to_id = {lab.get("name"): lab["id"] for lab in resp.get("labels", [])}
    found: list[str] = []
    missing: list[str] = []
    for name in label_names:
        if name in name_to_id:
            found.append(name_to_id[name])
        else:
            missing.append(name)
    return found, missing


def list_labels_messages(service, label_names: list[str], query: str | None = None,
                         user_id: str = "me") -> tuple[list[str], list[str]]:
    """聯集多個 label 的 message id（去重、保留首見順序）。

    回傳 (message id 清單, 找不到的 label 名稱清單)。找不到的 label 不會中斷，
    其餘 label 照常處理。
    """
    label_ids, missing = resolve_label_ids(service, label_names, user_id)
    seen: set[str] = set()
    ids: list[str] = []
    for lid in label_ids:
        for mid in _list_messages_for_label_id(service, lid, query, user_id):
            if mid not in seen:
                seen.add(mid)
                ids.append(mid)
    return ids, missing


def get_message_metadata(service, msg_id: str,
                         user_id: str = "me") -> tuple[str, str, str]:
    """只取 From/Subject/Date 標頭（format=metadata），不下載內文或附件。

    回傳 (sender, subject, date)。給 bootstrap 抽寄件者與 run 預先過濾用，
    避免為了讀標頭而下載整封信與 PDF 附件。
    """
    msg = _execute(service.users().messages().get(
        userId=user_id, id=msg_id, format="metadata",
        metadataHeaders=["From", "Subject", "Date"],
    ))
    headers = (msg.get("payload", {}) or {}).get("headers", []) or []
    return (
        header_value(headers, "From"),
        header_value(headers, "Subject"),
        header_value(headers, "Date"),
    )


def _build_email_message(service, msg_id: str, msg: dict, user_id: str) -> EmailMessage:
    payload = msg.get("payload", {}) or {}
    headers = payload.get("headers", []) or []
    texts, att_specs = walk_payload(payload)

    attachments: list[Attachment] = []
    for spec in att_specs:
        if spec["inline_data"]:
            data = decode_b64url(spec["inline_data"])
        elif spec["attachment_id"]:
            resp = _execute(service.users().messages().attachments().get(
                userId=user_id, messageId=msg_id, id=spec["attachment_id"],
            ))
            data = decode_b64url(resp["data"])
        else:
            data = b""
        attachments.append(Attachment(spec["filename"], spec["mime_type"], data))

    return EmailMessage(
        msg_id=msg_id,
        sender=header_value(headers, "From"),
        subject=header_value(headers, "Subject"),
        date=header_value(headers, "Date"),
        body_text="\n".join(texts),
        attachments=attachments,
    )


def extract_message(service, msg_id: str, user_id: str = "me") -> EmailMessage:
    msg = _execute(service.users().messages().get(
        userId=user_id, id=msg_id, format="full",
    ))
    return _build_email_message(service, msg_id, msg, user_id)


_BATCH_SIZE = 20  # Gmail batch endpoint 雖然單批上限是 100，但一次塞滿
                  # 100 個併發子請求很容易觸發「Too many concurrent requests
                  # for user」(429)，縮小批次以降低併發量。


def extract_messages(service, msg_ids: list[str], user_id: str = "me",
                     retries: int = 4, sleep=time.sleep) -> dict[str, EmailMessage]:
    """一次以 batch HTTP request 取多封信的完整內容，取代逐封呼叫
    `extract_message`：N 封信只需要 ceil(N/_BATCH_SIZE) 次 API round-trip，
    不是 N 次。附件仍各自用獨立請求下載（唯一的呼叫端 cathay_autocat 只掃沒
    有附件的每日通知信，這條路徑很少走到）。回傳 {msg_id: EmailMessage}。

    batch 內個別子請求的錯誤不會像 `_execute` 那樣自動重試（那是整批 HTTP
    請求本身的重試），所以這裡自己對每個 chunk 做退避重試：只重試 429/5xx
    這類暫時性錯誤，且只重試真正失敗的那幾封信，不必整個 chunk 重來；遇到
    非暫時性錯誤，或重試用盡，才拋出第一個錯誤。
    """
    from googleapiclient.errors import HttpError

    if not msg_ids:
        return {}
    results: dict[str, EmailMessage] = {}

    def _fetch(ids: list[str]) -> list[tuple[str, Exception]]:
        failures: list[tuple[str, Exception]] = []

        def _on_response(request_id, response, exception):
            if exception is not None:
                failures.append((request_id, exception))
                return
            results[request_id] = _build_email_message(service, request_id, response, user_id)

        batch = service.new_batch_http_request(callback=_on_response)
        for msg_id in ids:
            batch.add(
                service.users().messages().get(userId=user_id, id=msg_id, format="full"),
                request_id=msg_id,
            )
        _execute(batch)
        return failures

    for i in range(0, len(msg_ids), _BATCH_SIZE):
        pending = msg_ids[i:i + _BATCH_SIZE]
        for attempt in range(retries):
            failures = _fetch(pending)
            if not failures:
                break
            retryable_ids = [
                mid for mid, exc in failures
                if isinstance(exc, HttpError)
                and getattr(getattr(exc, "resp", None), "status", None) in _RETRY_STATUSES
            ]
            if len(retryable_ids) != len(failures) or attempt == retries - 1:
                raise failures[0][1]
            sleep(min(2 ** attempt, 8))
            pending = retryable_ids
    return results
