( function ( $ ) {
	"use strict";

	//Sales chart
	var ctx = document.getElementById( "sales-chart" );
	ctx.height = 150;
	var myChart = new Chart( ctx, {
		type: 'line',
		data: {
			labels: [ "2010", "2011", "2012", "2013", "2014", "2015", "2016" ],
			type: 'line',
			defaultFontFamily: 'Montserrat',
			datasets: [ {
				label: "Foods",
				data: [ 0, 30, 10, 120, 50, 63, 10 ],
				backgroundColor: 'transparent',
				borderColor: 'rgba(220,53,69,0.75)',
				borderWidth: 3,
				pointStyle: 'circle',
				pointRadius: 5,
				pointBorderColor: 'transparent',
				pointBackgroundColor: 'rgba(220,53,69,0.75)',
                    }, {
				label: "Electronics",
				data: [ 0, 50, 40, 80, 40, 79, 120 ],
				backgroundColor: 'transparent',
				borderColor: 'rgba(40,167,69,0.75)',
				borderWidth: 3,
				pointStyle: 'circle',
				pointRadius: 5,
				pointBorderColor: 'transparent',
				pointBackgroundColor: 'rgba(40,167,69,0.75)',
                    } ]
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
				xAxes: [ {
					display: true,
					gridLines: {
						display: false,
						drawBorder: false
					},
					scaleLabel: {
						display: false,
						labelString: 'Month'
					}
                        } ],
				yAxes: [ {
					display: true,
					gridLines: {
						display: false,
						drawBorder: false
					},
					scaleLabel: {
						display: true,
						labelString: 'Value'
					}
                        } ]
			},
			title: {
				display: false,
				text: 'Normal Legend'
			}
		}
	} );
} )( jQuery );