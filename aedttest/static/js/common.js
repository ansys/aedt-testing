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