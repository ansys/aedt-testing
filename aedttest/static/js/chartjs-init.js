function create_line_chart(ctx, x_axis, version_1, y_axis_1, version_2, y_axis_2) {
	new Chart(ctx, {
		type: 'line',
		data: {
			labels: x_axis,
			type: 'line',
			defaultFontFamily: 'Montserrat',
			datasets: [{
				label: version_1,
				data: y_axis_1,
				backgroundColor: 'transparent',
				borderColor: 'rgba(220,53,69,0.75)',
				borderWidth: 3,
				pointStyle: 'circle',
				pointRadius: 5,
				pointBorderColor: 'transparent',
				pointBackgroundColor: 'rgba(220,53,69,0.75)',
			}, {
				label: version_2,
				data: y_axis_2,
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
				xAxes: [{
					display: true,
					gridLines: {
						display: false,
						drawBorder: false
					},
					scaleLabel: {
						display: false,
						labelString: 'Month'
					}
				}],
				yAxes: [{
					display: true,
					gridLines: {
						display: false,
						drawBorder: false
					},
					scaleLabel: {
						display: true,
						labelString: 'Value'
					}
				}]
			},
			title: {
				display: false,
				text: 'Normal Legend'
			}
		}
	});
}