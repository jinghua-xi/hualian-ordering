"""Excel order attachments and SMTP delivery for the purchasing cart."""

from dataclasses import dataclass, field
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate
import hashlib
import io
import os
import re
import smtplib
import ssl
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile


DEFAULT_ORDER_RECIPIENT = "jiahuaqinan@gmail.com"
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOCUMENT_RELS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
INVALID_XML_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


class DeliveryError(Exception):
    """The SMTP server has not confirmed acceptance."""

    def __init__(self, message, *, retryable=True):
        super().__init__(message)
        self.retryable = retryable


def build_order_workbook(order):
    rows = [
        ("华联服装辅料采购订单",),
        ("订单号", order["number"]),
        ("客户名称", order["customer"]),
        ("提交时间（北京时间）", order["created_at"]),
        ("订单状态", order["status"]),
        (),
        ("货号", "商品", "品类", "颜色", "规格", "单位", "数量", "期望交付日期", "商品备注"),
    ]
    items = order.get("items")
    if items is None:  # Orders saved before carts had a single item at the top level.
        items = [order]
    for item in items:
        rows.append((item.get("manufacturer_sku") or item.get("sku", ""), *(
            item.get(field, "") for field in ("name", "category", "color", "spec", "unit", "quantity", "delivery_date", "note")
        )))

    worksheet = Element("worksheet", xmlns=SPREADSHEET_NS)
    views = SubElement(worksheet, "sheetViews")
    view = SubElement(views, "sheetView", workbookViewId="0")
    SubElement(view, "pane", ySplit="7", topLeftCell="A8", activePane="bottomLeft", state="frozen")
    columns = SubElement(worksheet, "cols")
    for index, width in enumerate((20, 34, 16, 14, 20, 10, 12, 22, 42), 1):
        SubElement(columns, "col", min=str(index), max=str(index), width=str(width), customWidth="1")
    sheet_data = SubElement(worksheet, "sheetData")
    for index, values in enumerate(rows, 1):
        row = SubElement(sheet_data, "row", r=str(index))
        for column, value in zip("ABCDEFGHI", values):
            cell = SubElement(row, "c", r=f"{column}{index}")
            if type(value) is int:
                SubElement(cell, "v").text = str(value)
            elif value is not None:
                cell.set("t", "inlineStr")
                inline = SubElement(cell, "is")
                text = SubElement(inline, "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
                text.text = INVALID_XML_TEXT.sub("\ufffd", str(value))
    SubElement(worksheet, "autoFilter", ref=f"A7:I{len(rows)}")

    workbook = Element("workbook", xmlns=SPREADSHEET_NS, attrib={"xmlns:r": DOCUMENT_RELS_NS})
    views = SubElement(workbook, "bookViews")
    SubElement(views, "workbookView")
    sheets = SubElement(workbook, "sheets")
    SubElement(sheets, "sheet", name="采购订单", sheetId="1", attrib={"r:id": "rId1"})

    content_types = Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    SubElement(content_types, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    SubElement(content_types, "Default", Extension="xml", ContentType="application/xml")
    SubElement(content_types, "Override", PartName="/xl/workbook.xml",
               ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    SubElement(content_types, "Override", PartName="/xl/worksheets/sheet1.xml",
               ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")

    root_relationships = Element("Relationships", xmlns=PACKAGE_RELS_NS)
    SubElement(root_relationships, "Relationship", Id="rId1", Type=f"{DOCUMENT_RELS_NS}/officeDocument",
               Target="xl/workbook.xml")
    sheet_relationships = Element("Relationships", xmlns=PACKAGE_RELS_NS)
    SubElement(sheet_relationships, "Relationship", Id="rId1", Type=f"{DOCUMENT_RELS_NS}/worksheet",
               Target="worksheets/sheet1.xml")

    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path, part in (
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", root_relationships),
            ("xl/workbook.xml", workbook),
            ("xl/_rels/workbook.xml.rels", sheet_relationships),
            ("xl/worksheets/sheet1.xml", worksheet),
        ):
            archive.writestr(path, tostring(part, encoding="utf-8", xml_declaration=True))
    return output.getvalue()


@dataclass(frozen=True)
class _SMTPSettings:
    host: str
    port: int
    security: str
    username: str
    password: str = field(repr=False)
    sender: Address
    recipient: Address


def _smtp_settings(recipient=None):
    host = os.environ.get("SMTP_HOST", "").strip()
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", "").strip() or username
    recipient = (os.environ.get("ORDER_RECIPIENT", DEFAULT_ORDER_RECIPIENT) if recipient is None else recipient).strip()
    security = os.environ.get("SMTP_SECURITY", "ssl").strip().lower()
    if not all((host, username, password)) or security not in {"ssl", "starttls"}:
        raise DeliveryError("发件配置不完整或无效。", retryable=False)
    default_port = "587" if security == "starttls" else "465"
    try:
        port = int(os.environ.get("SMTP_PORT", default_port))
        sender_address = Address(addr_spec=sender)
        recipient_address = Address(addr_spec=recipient)
    except (HeaderParseError, ValueError):
        raise DeliveryError("发件配置无效。", retryable=False) from None
    if not sender_address.domain or not recipient_address.domain or not 1 <= port <= 65535:
        raise DeliveryError("发件配置无效。", retryable=False)
    return _SMTPSettings(host, port, security, username, password, sender_address, recipient_address)


def mail_is_configured(recipient=None):
    try:
        _smtp_settings(recipient)
    except DeliveryError:
        return False
    return True


def send_order_email(order, *, recipient=None):
    settings = _smtp_settings(recipient)
    number = order["number"]
    filename = re.sub(r"[^A-Za-z0-9_-]", "_", number)
    identifier = hashlib.sha256(number.encode("utf-8")).hexdigest()
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = settings.recipient
    message["Subject"] = f"华联服装辅料采购订单 {number}"
    message["Date"] = formatdate(usegmt=True)
    message["Message-ID"] = f"<ordering-{identifier}@{settings.sender.domain}>"
    message.set_content(f"收到采购订单 {number}。商品、数量和期望交付日期详见附件。")
    message.add_attachment(build_order_workbook(order), maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           filename=f"{filename}.xlsx")

    client = None
    try:
        context = ssl.create_default_context()
        if settings.security == "ssl":
            client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=15, context=context)
        else:
            client = smtplib.SMTP(settings.host, settings.port, timeout=15)
            client.starttls(context=context)
        client.login(settings.username, settings.password)
        refusals = client.send_message(message, from_addr=settings.sender.addr_spec, to_addrs=[settings.recipient.addr_spec])
        if refusals:
            raise smtplib.SMTPRecipientsRefused(refusals)
    except smtplib.SMTPRecipientsRefused as error:
        code = error.recipients[settings.recipient.addr_spec][0]
        raise DeliveryError("发件服务拒绝了订单邮件的收件地址。", retryable=400 <= code < 500) from None
    except smtplib.SMTPResponseException as error:
        raise DeliveryError("发件服务未接受订单邮件。", retryable=400 <= error.smtp_code < 500) from None
    except (smtplib.SMTPNotSupportedError, ssl.SSLCertVerificationError, UnicodeError):
        raise DeliveryError("发件服务的安全连接或身份验证配置无效。", retryable=False) from None
    except OSError:
        raise DeliveryError("发件服务未确认接收订单邮件。") from None
    finally:
        if client is not None:
            try:
                client.close()
            except OSError:
                pass
