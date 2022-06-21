function badge_change(limit) {
    if( $('#threshold-slider').length ) {
        // only if slider exists update slider limit
        $(".thresh-elem").each(function () {
            if (($(this).data('delta') <= limit) || $(this).data('avg') < 3) {
                $(this).removeClass();
                $(this).addClass("thresh-elem badge badge-primary");
            } else {
                $(this).removeClass();
                $(this).addClass("thresh-elem badge badge-danger");
            }
        });
    }
}


function set_slider_limit() {
    if( $('#threshold-slider').length ) {
        // only if slider exists update slider limit
        let max_limit = 0;
        $(".thresh-elem.delta").each(function () {
            max_limit = Math.max(max_limit, $(this).data('delta'));
        });
        $('#threshold-slider').slider({ max: max_limit });
    }
}

set_slider_limit();