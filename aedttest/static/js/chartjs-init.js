function create_line_chart(
	ctx,
	x_data,
	x_label,
	y_label,
	version_ref,
	y_data_ref,
	version_now,
	y_data_now,
	diff
) {
	let datasets = [];
	// push reference and difference only if the exist
	if (y_data_ref && y_data_ref.length) {
		datasets.push({
				label: version_ref,
				data: y_data_ref,
				backgroundColor: 'transparent',
				borderColor: 'rgba(220,53,69,0.75)',
				borderWidth: 3,
				pointStyle: 'circle',
				pointRadius: 5,
				pointBorderColor: 'transparent',
				pointBackgroundColor: 'rgba(220,53,69,0.75)',
			})
	}

	datasets.push({
				label: version_now,
				data: y_data_now,
				backgroundColor: 'transparent',
				borderColor: 'rgba(40,167,69,0.75)',
				borderWidth: 3,
				pointStyle: 'circle',
				pointRadius: 5,
				pointBorderColor: 'transparent',
				pointBackgroundColor: 'rgba(40,167,69,0.75)',
			})

	if (diff && diff.length) {
		datasets.push({
				label: "Difference",
				data: diff,
				backgroundColor: 'transparent',
				borderColor: 'rgba(148,40,167,0.75)',
				borderWidth: 3,
				pointStyle: 'circle',
				pointRadius: 5,
				pointBorderColor: 'transparent',
				pointBackgroundColor: 'rgba(148,40,167,0.75)',
				hidden: true,
			})
	}

	return new Chart(ctx, {
		type: 'line',
		data: {
			labels: x_data,
			type: 'line',
			defaultFontFamily: 'Montserrat',
			datasets: datasets
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
					display: true,
					title: {
						display: true,
						text: y_label,
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