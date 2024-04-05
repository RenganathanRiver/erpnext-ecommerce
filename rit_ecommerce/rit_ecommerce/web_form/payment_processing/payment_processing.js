frappe.ready(function() {
    $("button[type='submit']").on("click", function(event) {
        event.preventDefault();
        var referenceName = $("input[data-fieldname='reference_name']").val();
		window.location.href = '/api/method/rit_ecommerce.custom.make_payment_request?dn=' + referenceName + '&dt=Sales Invoice&submit_doc=1&order_type=Shopping Cart';
    });
});
