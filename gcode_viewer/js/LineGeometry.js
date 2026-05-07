/**
 * @author WestLangley / http://github.com/WestLangley
 *
 */

THREE.LineGeometry = function () {

	THREE.LineSegmentsGeometry.call( this );

	this.type = 'LineGeometry';

};

THREE.LineGeometry.prototype = Object.assign(Object.create(THREE.LineSegmentsGeometry.prototype), {

    constructor: THREE.LineGeometry,

    isLineGeometry: true,

    setPositions: function (array, unpack = false ) {

		// converts [ x1, y1, z1,  x2, y2, z2, ... ] to pairs format
        if (unpack) {
            var length = array.length - 3;
            var points = new Float32Array(2 * length);

            for (var i = 0; i < length; i += 3) {

                points[2 * i] = array[i];
                points[2 * i + 1] = array[i + 1];
                points[2 * i + 2] = array[i + 2];

                points[2 * i + 3] = array[i + 3];
                points[2 * i + 4] = array[i + 4];
                points[2 * i + 5] = array[i + 5];

            }

            THREE.LineSegmentsGeometry.prototype.setPositions.call(this, points);
        }
        else
            THREE.LineSegmentsGeometry.prototype.setPositions.call(this, array);
		return this;

	},

    setColors: function (array, unpack = false  ) {

		// converts [ r1, g1, b1,  r2, g2, b2, ... ] to pairs format

        if (unpack) {
		    var length = array.length - 3;
		    var colors = new Float32Array( 2 * length );

		    for ( var i = 0; i < length; i += 3 ) {

			    colors[ 2 * i ] = array[ i ];
			    colors[ 2 * i + 1 ] = array[ i + 1 ];
			    colors[ 2 * i + 2 ] = array[ i + 2 ];

			    colors[ 2 * i + 3 ] = array[ i + 3 ];
			    colors[ 2 * i + 4 ] = array[ i + 4 ];
			    colors[ 2 * i + 5 ] = array[ i + 5 ];

		    }

		    THREE.LineSegmentsGeometry.prototype.setColors.call( this, colors );
        }
        else
            THREE.LineSegmentsGeometry.prototype.setColors.call(this, array);

		return this;

	},

	fromLine: function ( line ) {

		var geometry = line.geometry;

		if ( geometry.isGeometry ) {

			this.setPositions( geometry.vertices );

		} else if ( geometry.isBufferGeometry ) {

			this.setPositions( geometry.position.array ); // assumes non-indexed

		}

		// set colors, maybe

		return this;

	},

	copy: function ( /* source */ ) {

		// todo

		return this;

	}

} );
