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
