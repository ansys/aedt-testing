(function($) {
    "use strict";
    $(function () {
        for (var nk = window.location, o = $(".nano-content li a").filter(function () {
            return this.href == nk;
        })
            .addClass("active")
            .parent()
            .addClass("active"); ;) {
            if (!o.is("li")) break;
            o = o.parent()
                .addClass("d-block")
                .parent()
                .addClass("active");
        }
    });
})(jQuery);

function badge_change(limit) {
    $(".btn-plot").each(function () {
        if ($(this).data('delta') <= limit) {
            $(this).removeClass();
            $(this).addClass("btn btn-info btn-plot badge-primary");
        } else {
            $(this).removeClass();
            $(this).addClass("btn btn-info btn-plot badge-danger");
        }
    });
}

(function($) {
    "use strict";
    $(function () {
        badge_change(5);
    });
})(jQuery);


$('#threshold-slider').slider({
	formatter: function(value) {
		return 'Current value: ' + value;
	}
});


$('#threshold-slider').on('slide', function(slideEvt) {
	badge_change(slideEvt.value);
});