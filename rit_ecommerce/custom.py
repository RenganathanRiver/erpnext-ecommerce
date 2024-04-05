import frappe
from erpnext.accounts.party import get_party_bank_account
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_accounting_dimensions,
)
from frappe import _
from frappe.utils import flt, get_url
from erpnext.accounts.doctype.payment_request.payment_request import get_gateway_details, _get_payment_gateway_controller, get_dummy_message


from erpnext.accounts.doctype.payment_request.payment_request import (
    PaymentRequest as OriginalPaymentRequest,
)


class PaymentRequest(OriginalPaymentRequest):
    def on_payment_authorized(self, status=None):
        if not status:
            return

        if status not in ("Authorized", "Completed"):
            return

        if not hasattr(frappe.local, "session"):
            return

        if self.payment_channel == "Phone":
            return

        cart_settings = frappe.get_doc("Webshop Settings")

        if not cart_settings.enabled:
            return

        success_url = cart_settings.payment_success_url
        redirect_to = get_url("/orders/{0}".format(self.reference_name))

        if success_url:
            redirect_to = (
                {
                    "Orders": "/orders",
                    "Invoices": "/invoices",
                    "My Account": "/me",
                }
            ).get(success_url, "/me")

        self.set_as_paid()

        return redirect_to

    @staticmethod
    def get_gateway_details(args):
        if args.order_type != "Shopping Cart":
            return super().get_gateway_details(args)

        cart_settings = frappe.get_doc("Webshop Settings")
        gateway_account = cart_settings.payment_gateway_account
        return super().get_payment_gateway_account(gateway_account)
        
    def validate_payment_request_amount(self):
        existing_payment_request_amount = flt(
            get_existing_paid_payment_request_amount(self.reference_doctype, self.reference_name)
        )

        ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
        if not hasattr(ref_doc, "order_type") or getattr(ref_doc, "order_type") != "Shopping Cart":
            ref_amount = get_amount_for_validation(ref_doc, self.payment_account)

            if existing_payment_request_amount + flt(self.grand_total) > ref_amount:
                frappe.throw(
                    _("Total Payment Request amount cannot be greater than {0} amount").format(
                        self.reference_doctype
                    )
                )

    def get_payment_url(self):
        if self.reference_doctype != "Fees":
            data = frappe.db.get_value(
                self.reference_doctype, self.reference_name, ["company", "customer_name"], as_dict=1
            )
        else:
            data = frappe.db.get_value(
                self.reference_doctype, self.reference_name, ["student_name"], as_dict=1
            )
            data.update({"company": frappe.defaults.get_defaults().company})

        controller = _get_payment_gateway_controller(self.payment_gateway)
        controller.validate_transaction_currency(self.currency)

        if hasattr(controller, "validate_minimum_transaction_amount"):
            controller.validate_minimum_transaction_amount(self.currency, self.grand_total)

        if self.subject is not None:
            description = self.subject.encode("utf-8")
        else:
            description = ""
        return controller.get_payment_url(
            **{
                "amount": flt(self.grand_total, self.precision("grand_total")),
                "title": data.company.encode("utf-8"),
                "description": description,
                "reference_doctype": "Payment Request",
                "reference_docname": self.name,
                "payer_email": self.email_to or frappe.session.user,
                "payer_name": frappe.safe_encode(data.customer_name),
                "order_id": self.name,
                "currency": self.currency,
            }
        )
    
@frappe.whitelist()
def get_variant_url(item_code):
    try:
        route = frappe.get_value("Website Item", {"item_code": item_code}, "route")
        if route:
            return frappe.utils.get_url(route)
    except frappe.DoesNotExistError:
        frappe.throw(f"Item not created")

    except Exception as e:
        frappe.throw(f"Error fetching variant URL: {e}")
    return None

@frappe.whitelist(allow_guest=True)
def make_payment_request(**args):
    """Make payment request"""

    args = frappe._dict(args)
    ref_doc = frappe.get_doc(args.dt, args.dn)
    gateway_account = get_gateway_details(args) or frappe._dict()

    grand_total = get_amount(ref_doc, gateway_account.get("payment_account"))
    
    bank_account = (
        get_party_bank_account(args.get("party_type"), args.get("party"))
        if args.get("party_type")
        else ""
    )
    customer_email = frappe.db.get_value(
        "Customer",
        {"name":ref_doc.get("customer")}, "owner"
    )
    draft_payment_request = frappe.db.get_value(
        "Payment Request",
        {"reference_doctype": args.dt, "reference_name": args.dn, "docstatus": 0},
    )
    if draft_payment_request:
        frappe.db.set_value(
            "Payment Request", draft_payment_request, "grand_total", grand_total, update_modified=False
        )
        pr = frappe.get_doc("Payment Request", draft_payment_request)
    else:
        pr = frappe.new_doc("Payment Request")
        pr.update(
            {
                "payment_gateway_account": gateway_account.get("name"),
                "payment_gateway": gateway_account.get("payment_gateway"),
                "payment_account": gateway_account.get("payment_account"),
                "payment_channel": gateway_account.get("payment_channel"),
                "payment_request_type": args.get("payment_request_type"),
                "currency": ref_doc.currency,
                "grand_total": grand_total,
                "mode_of_payment": args.mode_of_payment,
                "email_to": args.recipient_id or customer_email or ref_doc.owner,
                "subject": _("Payment Request for {0}").format(args.dn),
                "message": gateway_account.get("message") or get_dummy_message(ref_doc),
                "reference_doctype": args.dt,
                "reference_name": args.dn,
                "party_type": args.get("party_type") or "Customer",
                "party": args.get("party") or ref_doc.get("customer"),
                "bank_account": bank_account,
            }
        )

        

        # Update dimensions
        pr.update(
            {
                "cost_center": ref_doc.get("cost_center"),
                "project": ref_doc.get("project"),
            }
        )

        for dimension in get_accounting_dimensions():
            pr.update({dimension: ref_doc.get(dimension)})

        if args.order_type == "Shopping Cart" or args.mute_email:
            pr.flags.mute_email = True

        pr.insert(ignore_permissions=True)
        if args.submit_doc:
            pr.submit()

    if args.order_type == "Shopping Cart":
        frappe.db.commit()
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = pr.get_payment_url()

    if customer_email:
            pr.update(
                {
                    "owner":customer_email,
                    "modified_by":customer_email,
                }
            )
    if args.return_doc:
        return pr
    return pr.as_dict()


@frappe.whitelist(allow_guest=True)
def make_partial_payment_request(**args):
    """Make Partial payment request"""

    args = frappe._dict(args)

    ref_doc = frappe.get_doc(args.dt, args.dn)
    gateway_account = get_gateway_details(args) or frappe._dict()


    bank_account = (
        get_party_bank_account(args.get("party_type"), args.get("party"))
        if args.get("party_type")
        else ""
    )
    partial_amount = float(args.get("partial_amount"))
    pr = frappe.new_doc("Payment Request")
    pr.update(
        {
            "payment_gateway_account": gateway_account.get("name"),
            "payment_gateway": gateway_account.get("payment_gateway"),
            "payment_account": gateway_account.get("payment_account"),
            "payment_channel": gateway_account.get("payment_channel"),
            "payment_request_type": args.get("payment_request_type"),
            "currency": ref_doc.currency,
            "grand_total": partial_amount,
            "mode_of_payment": args.mode_of_payment,
            "email_to": args.recipient_id or ref_doc.owner,
            "subject": _("Payment Request for {0}").format(args.dn),
            "message": gateway_account.get("message") or get_dummy_message(ref_doc),
            "reference_doctype": args.dt,
            "reference_name": args.dn,
            "party_type": args.get("party_type") or "Customer",
            "party": args.get("party") or ref_doc.get("customer"),
            "bank_account": bank_account,
        }
    )

    # Update dimensions
    pr.update(
        {
            "cost_center": ref_doc.get("cost_center"),
            "project": ref_doc.get("project"),
        }
    )

    for dimension in get_accounting_dimensions():
        pr.update({dimension: ref_doc.get(dimension)})

    pr.flags.mute_email = True

    pr.insert(ignore_permissions=True)
    if args.submit_doc:
        pr.submit()

    frappe.db.commit()
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = pr.get_payment_url()

    if args.return_doc:
        return pr

    return pr.as_dict()

def get_amount(ref_doc, payment_account=None):
    """get amount based on doctype"""
    dt = ref_doc.doctype
    if dt in ["Sales Order", "Purchase Order"]:
        grand_total = flt(ref_doc.rounded_total) or flt(ref_doc.grand_total)
    elif dt in ["Sales Invoice", "Purchase Invoice"]:
        if not ref_doc.get("is_pos"):
            if ref_doc.party_account_currency == ref_doc.currency:
                grand_total = flt(ref_doc.outstanding_amount)
            else:
                grand_total = flt(ref_doc.outstanding_amount) / ref_doc.conversion_rate
        elif dt == "Sales Invoice":
            for pay in ref_doc.payments:
                if pay.type == "Phone" and pay.account == payment_account:
                    grand_total = pay.amount
                    break
    elif dt == "POS Invoice":
        for pay in ref_doc.payments:
            if pay.type == "Phone" and pay.account == payment_account:
                grand_total = pay.amount
                break
    elif dt == "Fees":
        grand_total = ref_doc.outstanding_amount

    if grand_total > 0:
        return grand_total
    else:
        frappe.throw(_("Payment Entry is already created"))
        

def get_amount_for_validation(ref_doc, payment_account=None):
    """get amount based on doctype"""
    dt = ref_doc.doctype
    if dt in ["Sales Order", "Purchase Order"]:
        grand_total = flt(ref_doc.rounded_total) or flt(ref_doc.grand_total)
    elif dt in ["Sales Invoice", "Purchase Invoice"]:
        if not ref_doc.get("is_pos"):
            if ref_doc.party_account_currency == ref_doc.currency:
                grand_total = flt(ref_doc.grand_total)
            else:
                grand_total = flt(ref_doc.outstanding_amount) / ref_doc.conversion_rate
        elif dt == "Sales Invoice":
            for pay in ref_doc.payments:
                if pay.type == "Phone" and pay.account == payment_account:
                    grand_total = pay.amount
                    break
    elif dt == "POS Invoice":
        for pay in ref_doc.payments:
            if pay.type == "Phone" and pay.account == payment_account:
                grand_total = pay.amount
                break
    elif dt == "Fees":
        grand_total = ref_doc.outstanding_amount

    if grand_total > 0:
        return grand_total
    else:
        frappe.throw(_("Payment Entry is already created"))

def get_existing_paid_payment_request_amount(ref_dt, ref_dn):
    """
    Get the existing payment request amount which are paritially paid
    """
    existing_payment_request_amount = frappe.db.sql(
        """
        select sum(grand_total)
        from `tabPayment Request`
        where
            reference_doctype = %s
            and reference_name = %s
            and docstatus = 1
            and (status = 'Partially Paid')
    """,
        (ref_dt, ref_dn),
    )
    return flt(existing_payment_request_amount[0][0]) if existing_payment_request_amount else 0