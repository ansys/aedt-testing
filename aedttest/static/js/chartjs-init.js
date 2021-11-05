function create_line_chart(ctx, x_data, x_label, version_ref, y_data_ref, version_now, y_data_now) {
	new Chart(ctx, {
		type: 'line',
		data: {
			labels: x_data,
			type: 'line',
			defaultFontFamily: 'Montserrat',
			datasets: [{
				label: version_ref,
				data: y_data_ref,
				backgroundColor: 'transparent',
				borderColor: 'rgba(220,53,69,0.75)',
				borderWidth: 3,
				pointStyle: 'circle',
				pointRadius: 5,
				pointBorderColor: 'transparent',
				pointBackgroundColor: 'rgba(220,53,69,0.75)',
			}, {
				label: version_now,
				data: y_data_now,
				backgroundColor: 'transparent',
				borderColor: 'rgba(40,167,69,0.75)',
				borderWidth: 3,
				pointStyle: 'circle',
				pointRadius: 5,
				pointBorderColor: 'transparent',
				pointBackgroundColor: 'rgba(40,167,69,0.75)',
			}]
		},
		options: {
			responsive: true,

			tooltips: {
				mode: 'index',
				titleFontSize: 12,
				titleFontColor: '#000',
				bodyFontColor: '#000',
				backgroundColor: '#fff',
				titleFontFamily: 'Montserrat',
				bodyFontFamily: 'Montserrat',
				cornerRadius: 3,
				intersect: false,
			},
			legend: {
				display: false,
				labels: {
					usePointStyle: true,
					fontFamily: 'Montserrat',
				},
			},
			scales: {
                x: {
                    display: true,
                    title: {
                        display: true,
                        text: x_label,
                    },
                },
                y: {
                    display: false,
                    title: {
                        display: true,
                        text: "Value",
                    },
                },
            },
			title: {
				display: false,
				text: 'Normal Legend'
			}
		}
	});
}