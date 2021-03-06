from shapely.geometry import Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.strtree import STRtree
from shapely import affinity
from collections import defaultdict

from wasteoptimiser.optimiser.smallestenclosingcircle import make_circle as smallest_circle
try:
    from wasteoptimiser.nfp_interface.libnfporb_interface import genNFP
    _nfp_available = True
except:
    print("\nlibnfporb_interface.pyd not found under wasteoptimiser/nfp_interface/ or the binary is incompatible,\n" +
        "NFP option will not be available. Please see readme.md on instructions how to build the library.")
    _nfp_available = False


def roundCoords(coords, sgf=0):
    return [(round(c[0],sgf), round(c[1],sgf)) for c in coords]


def intCoords(coords):
    return [(int(c[0]), int(c[1])) for c in coords]

_debug = False

# extend Polygon to allow storing of NFPS
class Polygon(Polygon):
    shape_nfps = defaultdict() # keys are wkts of shapes + hole offset, values are NFPS
    name = "undefined" # friendly name to identify the shape
    position = [0,0]   # position of the shape on the board (reference point is circle_center)
    angle = 0          # angle of the shape
    origin = [0,0]


class Optimiser:
    def __init__(self, logger):
        self.logger = logger
        self.log_type = logger.logType.OPTIMISER

        self.width = 2400       # width of the board
        self.height = 1400      # height of the board
        self.edge_offset = 0    # offset from edge of board
        self.hole_offset = 0    # offset from hole
        self.preffered_pos = 0  # 0: top left, 1: top right, 2: bottom left, 3: botom right
        self.small_first = True # whether to first fill small holles
        self.hole_holes = []    # list of original holes in the board
        self.hole_shapes = []   # list of shapes inserted as holes
        self.shape = None       # shape to be placed
        self.centroid = [0, 0]  # centroid of shape to be placed
        self.circle_center = [0, 0] # center of smallest enclosing circle of shape
        self.circle_radius = 0  # radius of smallest enclosing circle of shape
        self.position = [0, 0]  # offset position of shape to be placed
        self.angle = 0          # angle (around centroid) of shape to be placed
        self.convex_hull = True # if true, work with the convex hull of the object

        self.startpolygons = [] # lsit of possible starting polygons (polygons along which boundaries to start optimisation)

    @property
    def holes(self):
        return [*self.hole_holes, *self.hole_shapes]

    @property
    def shape_rotated(self):
        return affinity.rotate(self.shape, self.angle, origin=(0,0))

    def getShapeHash(self):
        return hash(self.shape.wkt + str(self.hole_offset) + str(self.angle))

    def getBoardHolesCircle(self):
        """Returns shrinked board and dilated holes using smallest enclosing circle"""
        shrinkedboard = Polygon(self.getBoardShape()).buffer(-self.circle_radius - self.edge_offset)
        dilatedholes = [hole.buffer(self.circle_radius + self.hole_offset) for hole in self.holes]
        return [shrinkedboard, dilatedholes]

    def getBoardHolesNFP(self):
        """Returns shrinked board and dilated holes using NFP"""
        bounds = [abs(x[0]-x[1]) for x in zip(self.shape_rotated.bounds, [0, 0, self.width, self.height])]           
        shrinkedboard = box(*bounds).buffer(-self.edge_offset)
        dilatedholes = []
        for hole in self.holes:
            npolys = self.getNFPForHole(hole)
            if not npolys:
                self.logger.log(f"couldn't compute NFP for {hole.name}, falling back to circle", self.logger.logLevel.ERROR, self.log_type)
                npolys = hole.buffer(self.circle_radius + self.hole_offset)
            dilatedholes.append(npolys)
        return [shrinkedboard, dilatedholes]

    
    def getNFPForHole(self, hole):
        shape_hash = self.getShapeHash()
        try: # try to get cached NFP
            if _debug: print("hole ", hole.wkt, " has cached nfp")
            return hole.shape_nfps[shape_hash]
        except KeyError: # it does not exist
            shapepoints = list(orient(self.shape_rotated).exterior.coords)
            if self.convex_hull:
                shapepoints = list(orient(self.shape_rotated.convex_hull).exterior.coords)
            holepoints = list(orient(hole.simplify(1)).exterior.coords)
            holepoints = roundCoords(holepoints)
            trans = [- shapepoints[0][0], - shapepoints[0][1]]
            holepoints[0] = [holepoints[0][0]+1,holepoints[0][1]+1] #hacky hack
            holepoints[-1] = holepoints[0]
            try:
                nfps = genNFP(holepoints, shapepoints)
            except RuntimeError as err:
                self.logger.log("WTF?: " + str(err), self.logger.logLevel.DEBUG, self.log_type)
                holepoints = roundCoords(holepoints)
                try:
                    nfps = genNFP(holepoints, shapepoints)
                except Exception as ee:
                    self.logger.log("WTF!!!" + str(ee), self.logger.logLevel.DEBUG, self.log_type)
                    holepoints = intCoords(holepoints)
                    shapepoints = intCoords(shapepoints)
                    try:
                        nfps = genNFP(holepoints, shapepoints)
                    except Exception as ee:
                        self.logger.log("WTF??????????" + str(ee), self.logger.logLevel.DEBUG, self.log_type)
                        holepoints[0] = [holepoints[0][0]-1,holepoints[0][1]-1] #unhacky unhack
                        holepoints[-1] = holepoints[0]
                        try:
                            nfps = genNFP(holepoints, shapepoints)
                        except Exception as ee:
                            self.logger.log("Falling back to circle" + str(ee), self.logger.logLevel.DEBUG, self.log_type)
                            return None
            except:
                print("Fatal error")
            if _debug: print("storing new NFP for hole ", hole.wkt)

            try:
                npolys = Polygon(nfps[0], nfps[1:])
            except:
                # one of NFPS has less than 3 points -> ignote it #FIXME: do not ignore it somehow

                # hack to create polygons out of 1- and 2-point NFPS by appending
                # copies of the last point so that there are at least 3
                #nfps[1:] = [[*x, *[x[-1]]*(3-len(x))] if len(x) < 3 else x for x in nfps[1:]]

                npolys = Polygon(nfps[0], [x for x in nfps[1:] if len(x) >= 3])
                self.logger.log("OHSHIT!", logger.logLevel.WARNING, self.log_type)
            
            npolys = npolys.buffer(self.hole_offset, resolution=2)
            npolys = affinity.translate(npolys, trans[0], trans[1])            
            hole.shape_nfps[shape_hash] = npolys # store the NFP in cache
            return npolys


    def initStartpoly(self, nfp=True):
        """Prepares the board for placement optimisation"""
        shrinkedboard = None
        dilatedholes = []
        if not _nfp_available: nfp = False
        [shrinkedboard, dilatedholes] = self.getBoardHolesNFP() if nfp else self.getBoardHolesCircle()
        startpolygons = []
        for dhole in dilatedholes:
            shrinkedboard = shrinkedboard.difference(dhole).simplify(1)
        self.startpolygons = startpolygons
        if hasattr(shrinkedboard, "__getitem__"):
        #if multiple holes result from one subtraction
            for dpoly in shrinkedboard:
                self.startpolygons.append(dpoly)
        else:
            self.startpolygons.append(shrinkedboard)


    def getStartpoly(self):
        """Returns the start polygons as a list of lists of coordinates"""
        retpoly = []
        for stp in self.startpolygons:
            retpoly.append(list(stp.exterior.coords))
            for inner in stp.interiors:
                retpoly.append(list(inner.coords))
        return retpoly


    def addStartpoly(self):
        pass


    def begin(self):
        """Places the shape to an initial point. returns True if shape can be placed, False otherwise"""
        if not self.startpolygons:
            
            # import pdb;
            # pdb.set_trace()

            #print("ain't no place for this wicked")
            return False
        if self.small_first:            
            beginpolys = [min(self.startpolygons, key= lambda x: x.area)]
        else:
            beginpolys = self.startpolygons
        beginpoints = []
        for beginpoly in beginpolys:
            beginpoints.extend(list(beginpoly.exterior.coords))

        if self.preffered_pos == 0: # top left
            pref = lambda p: -p[0] + p[1] 
        elif self.preffered_pos == 1: # top right
            pref = lambda p: p[0] + p[1] 
        elif self.preffered_pos == 2: # bottom left
            pref = lambda p: -p[0] - p[1] 
        elif self.preffered_pos == 3: # bottom right
            pref = lambda p: p[0] - p[1] 
        elif self.preffered_pos == 4: # Left
            pref = lambda p: -p[0]
        elif self.preffered_pos == 5: # Right
            pref = lambda p: p[0] 
        elif self.preffered_pos == 6: # Right
            pref = lambda p: p[1] 
        beginpoint = max(beginpoints, key=pref)
        self.position = beginpoint
        if _debug: print(self.position)
        return True


    def step(self):
        """Performs one step of the local optimalisation and moves the shape
        to the new position. Returns True if the shape was moved, else returns False"""
        pass


    def addShapeAsHole(self, name='undefined'):
        """Adds a hole in the shape of the current shape with the current position"""
        newhole = Polygon(self.getShapeOriented())
        if self.convex_hull:
            newhole = newhole.convex_hull
        newhole.shape_nfps = defaultdict()
        newhole.name = name
        newhole.position = self.position
        newhole.angle = self.angle
        newhole.origin = affinity.rotate(Point(-self.circle_center[0], -self.circle_center[1]), self.angle, origin=(0,0))
        newhole.origin = affinity.translate(newhole.origin, self.position[0], self.position[1])
        self.hole_shapes.append(newhole)


    def setBoardSize(self, dimensions):
        """Sets dimensions of the board as (widht, height)"""
        self.width, self.height = dimensions


    def getBoardSize(self):
        """Returns dimensions of the board as (widht, height)"""
        return (self.width, self.height)


    def getBoardShape(self):
        """Returns coordinates of the board rectangle in counter clocwise
            order, starting from the bottom left
        """
        w = self.width
        h = self.height
        return ((0, 0), (w, 0), (w, h), (0, h), (0, 0))


    def addHole(self, shape):
        """Adds a hole. Expecting a list of points ((x, y), ...)
            If the new hole intersects any existing one, it merges with it"""
        new_hole = orient(Polygon(shape))
        if not new_hole.is_valid: return False # TODO: Error code
        new_hole.shape_nfps = defaultdict() # clear cached NFPS
        holes_to_remove = []
        for hole in self.holes:
            if new_hole.intersects(hole) and not new_hole.touches(hole):
                try:
                    new_hole = Polygon(new_hole.union(hole).exterior.coords) # throw out interior
                    new_hole.shape_nfps = defaultdict() # clear cached NFPS
                except:
                    print("error in adding hole")
                holes_to_remove.append(hole)
        for hole in holes_to_remove:
            try:
                self.hole_holes.remove(hole)
            except:
                self.hole_shapes.remove(hole)
        self.hole_holes.append(new_hole)


    def subtractHole(self, shape):
        """Subtracts a hole. Expecting a list of points ((x, y), ...)"""
        not_hole = Polygon(shape)
        holes_to_remove = []
        holes_to_add = []
        for hole in self.holes:
            if hole.contains(not_hole):
                return
            if not_hole.contains(hole):
                holes_to_remove.append(hole)
            elif not_hole.intersects(hole):
                holes_to_add.append(hole.difference(not_hole))
                holes_to_remove.append(hole)
        for hole in holes_to_remove:
            try:
                self.hole_holes.remove(hole)
            except:
                self.hole_shapes.remove(hole)
        for hole in holes_to_add:
            if hasattr(hole, "__getitem__"):
            #if multiple holes result from one subtraction
                for h in hole:
                    h.shape_nfps = defaultdict() # clear NFP cache
                    self.hole_holes.append(h)    
            else:
                hole.shape_nfps = defaultdict() # clear NFP cache
                self.hole_holes.append(hole)


    def getHoles(self, htype='all'):
        """Returns the holes as list of lists of points
            [Hole1((x, y), ...), Hole2((x, y), ...)]
        """
        if htype == 'holes':
            holes = self.hole_holes
        elif htype == 'shapes':
            holes = self.hole_shapes
        else:
            holes = self.holes
        ret_holes = []
        for hole in holes:
            ret_holes.append(list(hole.boundary.coords))
        return ret_holes


    def removeHole(self, hole):
        """Removes a hole, expects a instance of a hole (which shluld exist in self.holes)"""
        try:
            self.hole_holes.remove(hole)
        except:
            self.hole_shapes.remove(hole)


    def queryHole(self, point):
        """Returns a hole objects that contains the point"""
        for hole in self.holes:
            if Point(point[0], point[1]).within(hole):
                return hole
        return None


    def setShape(self, shape):
        """Sets the working shape. Expecting a list of points"""
        self.shape = orient(Polygon(roundCoords(shape,5)).simplify(1))
        *self.circle_center, self.circle_radius = smallest_circle(self.shape.exterior.coords) # [x, y, r]
        self.shape = affinity.translate(self.shape, -self.circle_center[0], -self.circle_center[1])
        centroid = self.shape.centroid
        self.centroid = [centroid.x, centroid.y]


    def getShape(self):
        """Returns a list of coordinates of the target shape in the default position"""
        return list(affinity.translate(self.shape, self.circle_center[0], self.circle_center[1]).boundary.coords)


    def getShapeOriented(self):
        """Returns a list of coordinates of the target shape in the current position and reotation"""
        rotated = affinity.rotate(self.shape, self.angle, origin=(0,0))
        translatedrotated = affinity.translate(rotated, self.position[0], self.position[1])
        return list(translatedrotated.boundary.coords)


    def getShapeOrientedDilated(self):
        """"Returns the target shape dillated by the given amount"""
        rotated = affinity.rotate(self.shape.buffer(self.circle_radius/2), self.angle, origin=(0,0))
        translatedrotated = affinity.translate(rotated, self.position[0], self.position[1])
        return list(translatedrotated.boundary.coords)

    
    def getShapeNamesPositions(self):
        for shape in sorted(self.hole_shapes, key=lambda x: x.position[0]):
            print(shape.name, " ", shape.position, " ", shape.angle)

    def getArea(self, shape):
        """Returns area of the polygon. Expecting a list of points ((x, y), ...)"""
        return Polygon(shape).area



if __name__ == "__main__":
    opt = Optimiser()
    h = opt.getHoles()
    #print(h)