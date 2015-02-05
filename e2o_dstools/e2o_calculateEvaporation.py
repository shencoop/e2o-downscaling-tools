"""
Get a variable from the forcing data from the e2o server for a specific region and time range


usage:

    e2o_getvar.py -I inifile

    -I inifile - ini file with settings which data to get
"""

#TODO: Add local cache
#TODO: Add tiling -> get small chunks
#TODO: add caching

import getopt, sys, os, netCDF4, glob
import osgeo.gdal as gdal
from osgeo.gdalconst import *
from osgeo import gdal, gdalconst
import datetime
from numpy import *
import numpy as np
from e2o_utils import *
import pdb
import pandas as pd
import pcraster as pcr
from scipy import interpolate
import scipy.ndimage
import shutil

#pcr.setglobaloption("radians")

#ncurl = "http://wci.earth2observe.eu/thredds/dodsC/ecmwf/met_forcing_v0/1980/Tair_daily_E2OBS_198001.nc"

def usage(*args):
    """
    Print usage information

    -  *args: command line arguments given
    """
    sys.stdout = sys.stderr
    for msg in args: print msg
    print __doc__
    sys.exit(0)

class ncdatset():
    """
    Wrapper around the nc object to simplify things
    
    Opens the dataset and determines the number of dimensions
    3 = T, Lat Lon
    4 = T heigth, Lat Lon
    """
    def __init__(self,ncurl,logger):
        self.logger = logger
        try:
            self.nc = netCDF4.Dataset(ncurl)
        except:
            self.logger.error("Failed to open remote file: " + ncurl)
            sys.exit(12)

        self.lat = self.getlat(self.nc)
        if self.lat == None:
            self.logger.error("No lat information found!")
        self.lon = self.getlon(self.nc)
        if self.lon == None:
            self.logger.error("No lon information found!")

        self.heigth = self.getheigth(self.nc)
        self.dimensions = 3 if self.heigth == None else 4
        self.time =   self.gettime(self.nc)
        self.timesteps = self.time.shape[0]
        self.logger.debug(self.nc)


    def __del__(self):
        self.nc.close()
        
        
    def getlat(self,ncdataset):
        """
        """

        for a in ncdataset.variables:
            if  ncdataset.variables[a].standard_name == 'latitude':
                return ncdataset.variables[a]

        return None

    def getvarbyname(self,name):
        """
        """


        for a in self.nc.variables:
            if  self.nc.variables[a].standard_name == name:
                return self.nc.variables[a]

        return None

    def gettime(self,ncdataset):
        """
        """

        for a in ncdataset.variables:
            if  ncdataset.variables[a].standard_name == 'time':
                return ncdataset.variables[a]

        return None
        

    def getlon(self,ncdataset):
        """
        """

        for a in ncdataset.variables:
            if  ncdataset.variables[a].standard_name == 'longitude':
                return ncdataset.variables[a]

        return None


    def getheigth(self,ncdataset):
        """
        """

        for a in ncdataset.variables:
            if  ncdataset.variables[a].standard_name == 'height':
                return ncdataset.variables[a]

        return None

def readMap(fileName, fileFormat,logger):
    """
    Read geographical file into memory
    """
  
    #Open file for binary-reading

    mapFormat = gdal.GetDriverByName(fileFormat)
    mapFormat.Register()
    ds = gdal.Open(fileName)
    if ds is None:
        logger.error('Could not open ' + fileName + '. Something went wrong!! Shutting down')
        sys.exit(1)
    # Retrieve geoTransform info
    geotrans = ds.GetGeoTransform()
    originX = geotrans[0]
    originY = geotrans[3]
    resX    = geotrans[1]
    resY    = geotrans[5]
    cols = ds.RasterXSize
    rows = ds.RasterYSize
    x = linspace(originX+resX/2,originX+resX/2+resX*(cols-1),cols)
    y = linspace(originY+resY/2,originY+resY/2+resY*(rows-1),rows)
    # Retrieve raster
    RasterBand = ds.GetRasterBand(1) # there's only 1 band, starting from 1
    data = RasterBand.ReadAsArray(0,0,cols,rows)
    FillVal = RasterBand.GetNoDataValue()
    RasterBand = None
    del ds
    return resX, resY, cols, rows, x, y, data, FillVal

def get_times_daily(startdate,enddate, serverroot, wrrsetroot, filename,logger):
    """
    generate a dictionary with date/times and the NC files in which the data resides
    """

    numdays = enddate - startdate
    dateList = []
    filelist = {}
    for x in range (0, numdays.days + 1):
        dateList.append(startdate + datetime.timedelta(days = x))
    
    for thedate in dateList:
        ncfile = serverroot + wrrsetroot + "%d" % (thedate.year) + "/" + filename + "%d%02d.nc" % (thedate.year,thedate.month)
        filelist[str(thedate)] = ncfile
    
    return filelist, dateList


def get_times(startdate,enddate, serverroot, wrrsetroot, filename, timestepSeconds, logger):
    """
    generate a dictionary with date/times and the NC files in which the data resides for flexible timestep
    """

    numdays = enddate - startdate
    dateList = []
    filelist = {}
    #days
    for x in range (0, numdays.days + 1):
        #selected time-step in seconds
        delta = 0
        while delta < 86400:
            dateList.append(startdate + datetime.timedelta(seconds = delta))
            delta += timestepSeconds
    
    for thedate in dateList:
        ncfile = serverroot + wrrsetroot + "%d" % (thedate.year) + "/" + filename + "%d%02d.nc" % (thedate.year,thedate.month)
        filelist[str(thedate)] = ncfile
    
    return filelist, dateList


class getstepdaily():
    """
    class to get data from a set of NC files
    Initialise with a list of netcdf files and a variable name (standard_name)

    """
    
    def __init__(self,nclist,BB,varname,logger):
        """
        """
        self.o_nc_files = []
        self.list = nclist
        self.varname = varname
        self.BB = BB
        self.latidx = []
        self.lonidx =[]
        self.lat=[]
        self.lon=[]
        self.data = []
        self.logger = logger

    def getdate(self,thedate):

        datestr = str(thedate)
        lat = None
        lon = None
        window = None
        
        if datestr in self.list.keys():
            self.dset = ncdatset(self.list[datestr],self.logger)
            data = self.dset.getvarbyname(self.varname)
            lat = flipud(self.dset.lat[:])
            
            lon = self.dset.lon[:]
            (latidx,) = logical_and(lat >= self.BB['lat'][0], lat < self.BB['lat'][1]).nonzero()
            (lonidx,) = logical_and(lon >= self.BB['lon'][0], lon < self.BB['lon'][1]).nonzero()

            time = self.dset.time
            timeObj = netCDF4.num2date(time[:], units=time.units, calendar=time.calendar)
            
            dpos = thedate.day -1
            
            if self.dset.dimensions ==3:
                window = data[dpos,latidx.min():latidx.max()+1,lonidx.min():lonidx.max()+1]
            if self.dset.dimensions ==4:
                window = data[dpos,0,latidx.min():latidx.max()+1,lonidx.min():lonidx.max()+1]
            
            self.lat = lat[latidx]
            self.lon = lon[lonidx]
    
        else:
            self.logger.error( "cannot find: " + datestr)
            
        return self.lat, self.lon, window

    def getdates(self,alldates):
        """
        Does not work yet
        """
        lat = None
        lon = None
        ret = []
        
        lastnc = None
        
        # here we loop over nc files for speed reasons
        
        for theone in  unique(self.list.values()):
            self.dset = ncdatset(theone,self.logger)
            
            time = self.dset.time
            tar = time[:]
            timeObj = netCDF4.num2date(tar, units=time.units, calendar=time.calendar)
            #print timeObj
            spos = nonzero(timeObj == alldates[0])[0]
            if len(spos) != 1:
                spos = 0
            else:
                spos = int(spos)
 
            epos = nonzero(timeObj == alldates[-1])[0]
            if len(epos) != 1:
                epos = len(tar)
            else:
                epos = int(epos + 1)
            
            
            self.logger.info("Processing url: " + theone )
            
            data = self.dset.getvarbyname(self.varname)
            
            if data == None:
                self.logger.error("dataset with standard_name " + self.varname + " not found" )
                
            lat = self.dset.lat[:]
            lon = self.dset.lon[:]

            (self.latidx,) = logical_and(lat >= self.BB['lat'][0], lat <= self.BB['lat'][1]).nonzero()
            (self.lonidx,) = logical_and(lon >= self.BB['lon'][0], lon <= self.BB['lon'][1]).nonzero()


            if self.dset.dimensions ==3:
                window = data[spos:epos,self.latidx.min():self.latidx.max()+1,self.lonidx.min():self.lonidx.max()+1]
            if self.dset.dimensions ==4:
                window = data[spos:epos,0,self.latidx.min():self.latidx.max()+1,self.lonidx.min():self.lonidx.max()+1]

            
            self.lat = lat[self.latidx]
            self.lon = lon[self.lonidx]
            
            if len(ret) == 0:
                ret = window.copy()
            else:
                ret = vstack((ret,window))
        
        return ret
        
    def getdates_seconds(self,alldates):
        """
        Does not work yet
        """
        lat = None
        lon = None
        ret = []
        
        lastnc = None
        
        # here we loop over nc files fro speed reasons
        
        for theone in  unique(self.list.values()):
            self.dset = ncdatset(theone,self.logger)
            
            time = self.dset.time
            tar = time[:]
            timeObj = netCDF4.num2date(tar, units=time.units, calendar=time.calendar)
            #print timeObj
            spos = nonzero(timeObj == alldates[0])[0]
            if len(spos) != 1:
                spos = 0
            else:
                spos = int(spos)
 
            epos = nonzero(timeObj == alldates[-1])[0]
            if len(epos) != 1:
                epos = len(tar)
            else:
                epos = int(epos + 1)
            
            
            self.logger.info("Processing url: " + theone )
            
            data = self.dset.getvarbyname(self.varname)
            
            if data == None:
                self.logger.error("dataset with standard_name " + self.varname + " not found" )
                
            lat = self.dset.lat[:]
            lon = self.dset.lon[:]

            (self.latidx,) = logical_and(lat >= self.BB['lat'][0], lat <= self.BB['lat'][1]).nonzero()
            (self.lonidx,) = logical_and(lon >= self.BB['lon'][0], lon <= self.BB['lon'][1]).nonzero()


            if self.dset.dimensions ==3:
                window = data[spos:epos,self.latidx.min():self.latidx.max()+1,self.lonidx.min():self.lonidx.max()+1]
            if self.dset.dimensions ==4:
                window = data[spos:epos,0,self.latidx.min():self.latidx.max()+1,self.lonidx.min():self.lonidx.max()+1]

            
            self.lat = lat[self.latidx]
            self.lon = lon[self.lonidx]
            
            if len(ret) == 0:
                ret = window.copy()
            else:
                ret = vstack((ret,window))
        
        return ret
        
class getstep():
    """
    class to get data from a set of NC files for user defined timestep in seconds
    Initialise with a list of netcdf files and a variable name (standard_name)

    """
    
    def __init__(self,nclist,BB,varname,timestepSeconds,logger):
        """
        """
        self.o_nc_files = []
        self.list = nclist
        self.varname = varname
        self.BB = BB
        self.latidx = []
        self.lonidx =[]
        self.lat=[]
        self.lon=[]
        self.data = []
        self.logger = logger

    def getdate(self,thedate):

        datestr = str(thedate)
        lat = None
        lon = None
        window = None
        
        if datestr in self.list.keys():
            self.dset = ncdatset(self.list[datestr],self.logger)
            data = self.dset.getvarbyname(self.varname)
            lat = flipud(self.dset.lat[:])
            
            lon = self.dset.lon[:]
            (latidx,) = logical_and(lat >= self.BB['lat'][0], lat < self.BB['lat'][1]).nonzero()
            (lonidx,) = logical_and(lon >= self.BB['lon'][0], lon < self.BB['lon'][1]).nonzero()

            time = self.dset.time
            timeObj = netCDF4.num2date(time[:], units=time.units, calendar=time.calendar)
            
            if timestepSecond < 3600:
                dpos = thedate.second -1
            elif timestepSecond < 86400:
                dpos = thedate.hour -1
            else:
                dpos = thedate.day -1
            
            if self.dset.dimensions ==3:
                window = data[dpos,latidx.min():latidx.max()+1,lonidx.min():lonidx.max()+1]
            if self.dset.dimensions ==4:
                window = data[dpos,0,latidx.min():latidx.max()+1,lonidx.min():lonidx.max()+1]
            
            self.lat = lat[latidx]
            self.lon = lon[lonidx]
    
        else:
            self.logger.error( "cannot find: " + datestr)
            
        return self.lat, self.lon, window

    def getdates(self,alldates):
        """
        Does not work yet
        """
        lat = None
        lon = None
        ret = []
        
        lastnc = None
        
        # here we loop over nc files fro speed reasons
        
        for theone in  unique(self.list.values()):
            self.dset = ncdatset(theone,self.logger)
            
            time = self.dset.time
            tar = time[:]
            timeObj = netCDF4.num2date(tar, units=time.units, calendar=time.calendar)
            #print timeObj
            spos = nonzero(timeObj == alldates[0])[0]
            if len(spos) != 1:
                spos = 0
            else:
                spos = int(spos)
 
            epos = nonzero(timeObj == alldates[-1])[0]
            if len(epos) != 1:
                epos = len(tar)
            else:
                epos = int(epos + 1)
            
            
            self.logger.info("Processing url: " + theone )
            
            data = self.dset.getvarbyname(self.varname)
            
            if data == None:
                self.logger.error("dataset with standard_name " + self.varname + " not found" )
                
            lat = self.dset.lat[:]
            lon = self.dset.lon[:]

            (self.latidx,) = logical_and(lat >= self.BB['lat'][0], lat <= self.BB['lat'][1]).nonzero()
            (self.lonidx,) = logical_and(lon >= self.BB['lon'][0], lon <= self.BB['lon'][1]).nonzero()


            if self.dset.dimensions ==3:
                window = data[spos:epos,self.latidx.min():self.latidx.max()+1,self.lonidx.min():self.lonidx.max()+1]
            if self.dset.dimensions ==4:
                window = data[spos:epos,0,self.latidx.min():self.latidx.max()+1,self.lonidx.min():self.lonidx.max()+1]

            
            self.lat = lat[self.latidx]
            self.lon = lon[self.lonidx]
            
            if len(ret) == 0:
                ret = window.copy()
            else:
                ret = vstack((ret,window))
        
        return ret


def getmapname(number,prefix):
    """
    generate a pcraster type mapname based on timestep and prefix
    :var number: number of the mape
    :var prefix: prefix for the map

    :return: Name
    """
    below_thousand = number % 1000
    above_thousand = number / 1000
    mapname = str(prefix + '%0' + str(8-len(prefix)) + '.f.%03.f') % (above_thousand, below_thousand)

    return mapname

def save_as_mapsstack(lat,lon,data,times,directory,prefix="E2O",oformat="PCRaster"):        
    
    cnt = 0
    if not os.path.exists(directory):
        os.mkdir(directory)
    for a in times:
            mapname = getmapname(cnt,prefix)
            #print "saving map: " + os.path.join(directory,mapname)
            writeMap(os.path.join(directory,mapname),oformat,lon,lat[::-1],flipud(data[cnt,:,:]),-999.0)
            cnt = cnt + 1    

def save_as_mapsstack_per_day(lat,lon,data,ncnt,directory,prefix="E2O",oformat="PCRaster"):        
    
    if not os.path.exists(directory):
        os.mkdir(directory)
    mapname = getmapname(ncnt,prefix)
    #print "saving map: " + os.path.join(directory,mapname)
    writeMap(os.path.join(directory,mapname),oformat,lon,lat[::-1],flipud(data[:,:]),-999.0)

def save_as_gtiff(lat,lon,data,ncnt,directory,prefix,oformat='GTiff'):        
    
    if not os.path.exists(directory):
        os.mkdir(directory)
    mapname = prefix + '.tif'
    #print "saving map: " + os.path.join(directory,mapname)
    writeMap(os.path.join(directory,mapname),oformat,lon,lat[::-1],flipud(data[:,:]),-999.0)
    
def resampleDEM(folderHighResDEM, folderLowResDEM,logger):
    
    #create temp dir
    try:
        os.stat('temp')
    except:
        os.mkdir('temp')
    
    # Source
    src_filename    = os.path.join(folderLowResDEM,'DEM.tif')
    src             = gdal.Open(src_filename, gdalconst.GA_ReadOnly)
    src_proj        = src.GetProjection()
    src_geotrans    = src.GetGeoTransform()

    # We want a section of source that matches this:
    match_filename  = os.path.join(folderHighResDEM,'DEM.tif')
    match_ds        = gdal.Open(match_filename, gdalconst.GA_ReadOnly)
    match_proj      = match_ds.GetProjection()
    match_geotrans  = match_ds.GetGeoTransform()
    wide            = match_ds.RasterXSize
    high            = match_ds.RasterYSize

    # Output / destination
    dst_filename = os.path.join('temp','DEM.tif')
    dst = gdal.GetDriverByName('GTiff').Create(dst_filename, wide, high, 1, gdalconst.GDT_Float32)
    dst.SetGeoTransform( match_geotrans )
    dst.SetProjection( match_proj)

    # Do the work
    gdal.ReprojectImage(src, dst, src_proj, match_proj, gdalconst.GRA_NearestNeighbour)

    del dst # Flush

    resX, resY, cols, rows, XI, YI, resLowResDEM, FillVal = readMap(dst_filename,'GTiff',logger)
    resX, resY, cols, rows, XI, YI, highResDEM, FillVal = readMap(match_filename,'GTiff',logger)

    elevationCorrection = highResDEM - resLowResDEM
       
    return elevationCorrection, highResDEM, resLowResDEM
    
def resample(highResFolder,prefix,ncnt,logger):
    
    #create resample dir
    try:
        os.stat('resampled')
    except:
        os.mkdir('resampled')    
    
    tif_mapname         = prefix+'.tif'
    pcraster_mapname    = getmapname(ncnt,prefix)
    
    tif_filename        = os.path.join('temp',tif_mapname)
    pcraster_filename   = os.path.join('temp',pcraster_mapname)
    pcraster_resFilename   = os.path.join('resampled',pcraster_mapname)
    
    command= 'gdal_translate -of %s %s %s' % ('GTiff',pcraster_filename,tif_filename)
    os.system(command)
      
    # Source
    src_filename    = os.path.join('temp',tif_mapname)
    src             = gdal.Open(src_filename, gdalconst.GA_ReadOnly)
    src_proj        = src.GetProjection()
    src_geotrans    = src.GetGeoTransform()

    # We want a section of source that matches this:
    match_filename  = os.path.join(highResFolder,'DEM.tif')
    match_ds        = gdal.Open(match_filename, gdalconst.GA_ReadOnly)
    match_proj      = match_ds.GetProjection()
    match_geotrans  = match_ds.GetGeoTransform()
    wide            = match_ds.RasterXSize
    high            = match_ds.RasterYSize

    # Output / destination
    dst_filename = os.path.join('resampled',tif_mapname)
    dst = gdal.GetDriverByName('GTiff').Create(dst_filename, wide, high, 1, gdalconst.GDT_Float32)
    dst.SetGeoTransform( match_geotrans )
    dst.SetProjection( match_proj)

    # Do the work
    gdal.ReprojectImage(src, dst, src_proj, match_proj, gdalconst.GRA_NearestNeighbour)

    del dst # Flush
       
    resX, resY, cols, rows, x, y, data, FillVal = readMap(dst_filename,'GTiff',logger)

    # nodig?? data    = np.flipud(dataUD)
    
    return data

def correctRsin(Rsin,currentdate,radiationCorDir,logger):
    #get day of year
    tt  = currentdate.timetuple()
    JULDAY = tt.tm_yday
    #read data from radiation correction files
    mapname     = getmapname(JULDAY,'FLAT')
    fileName    = os.path.join(radiationCorDir,mapname)
    resX, resY, cols, rows, x, y, data, FillVal          = readMap(fileName,'PCRaster',logger)
    
    resX, resY, cols, rows, x, y, flat, FillVal            = readMap((os.path.join(radiationCorDir,(getmapname(JULDAY,'FLAT')))),'PCRaster',logger)   
    resX, resY, cols, rows, x, y, flatdir, FillVal         = readMap((os.path.join(radiationCorDir,(getmapname(JULDAY,'FLATDIR')))),'PCRaster',logger)
    resX, resY, cols, rows, x, y, cor, FillVal             = readMap((os.path.join(radiationCorDir,(getmapname(JULDAY,'COR')))),'PCRaster',logger)
    resX, resY, cols, rows, x, y, cordir, FillVal          = readMap((os.path.join(radiationCorDir,(getmapname(JULDAY,'CORDIR')))),'PCRaster',logger)
    #ratio direct - diffuse
    ratio           = flatdir / flat
    Rsin_dir        = ratio * Rsin
    #corrected Rsin direct for elevation and slope
    Rsin_dir_cor    = (cordir/flatdir)*Rsin_dir
    Rsin_cor        = Rsin_dir_cor + (Rsin - Rsin_dir)
    
    return Rsin_cor

def PenmanMonteith(lat, currentdate, relevantDataFields, Tmax, Tmin, elevationCorrection, highResDEM, resLowResDEM, downscaling):
    
    """
    relevantDataFields : ['Temperature','DownwellingLongWaveRadiation','SurfaceAtmosphericPressure',\
                    'NearSurfaceSpecificHumidity','SurfaceIncidentShortwaveRadiation','NearSurfaceWindSpeed']
    """
    Tmean   =  relevantDataFields[0]
    Rlin    =  relevantDataFields[1]
    Pres    =  relevantDataFields[2]
    Q       =  relevantDataFields[3]
    Rsin    =  relevantDataFields[4]
    Wsp     =  relevantDataFields[5]
              
    """
    Computes Penman-Monteith reference evaporation
    Inputs:
        Rsin:        netCDF obj or NumPy array   -- 3D array (time, lat, lon) incoming shortwave radiation [W m-2]
        Rlin:        netCDF obj or NumPy array   -- 3D array (time, lat, lon) incoming longwave radiation [W m-2]
        Tmean:       netCDF obj or NumPy array   -- 3D array (time, lat, lon) daily mean temp [K]
        Tmax:        netCDF obj or NumPy array   -- 3D array (time, lat, lon) daily max. temp [K]
        Tmin:        netCDF obj or NumPy array   -- 3D array (time, lat, lon) daily min. temp [K]
        Wsp:         netCDF obj or NumPy array   -- 3D array (time, lat, lon) wind speed [m s-2]
        Q:           netCDF obj or NumPy array   -- 3D array (time, lat, lon) specific humididy [kg kg-1]
        Pres:        netCDF obj or NumPy array   -- 3D array (time, lat, lon) Surface air pressure [Pa]
        Pet:         netCDF obj or NumPy array   -- 3D array (time, lat, lon) for target data
    Outputs:
        trg_var:    netCDF obj or NumPy array   -- 3D array (time, lat, lon) for target data, updated with computed values
    """
    cp           = 1013         # specific heat of air 1013 [J kg-1 K-1]
    TimeStepSecs = 86400        # timestep in seconds
    karman       = 0.41         # von Karman constant [-]
    vegh         = 0.12         # vegetation height [m] 
    refh         = 2            # reference height where wind speed is measured
    alpha        = 0.23         # albedo, 0.23 [-]
    rs           = 70           # surface resistance, 70 [s m-1]
    R            = 287.058      # Universal gas constant [J kg-1 K-1]
    convmm       = 1000*TimeStepSecs # conversion from meters to millimeters
    sigma        = 4.903e-9     # stephan boltzmann [W m-2 K-4]
    eps          = 0.622        # ratio of water vapour/dry air molecular weights [-]
    g            = 9.81         # gravitational constant [m s-2]
    R_air        = 8.3144621    # specific gas constant for dry air [J mol-1 K-1]
    Mo           = 0.0289644    # molecular weight of gas [g / mol]
    lapse_rate   = 0.006        # lapse rate [K m-1]
    
    if downscaling == 'True':
        #apply elevation correction
        Tmean_cor   = Tmean - lapse_rate * elevationCorrection
        Tmin_cor    = Tmin - lapse_rate * elevationCorrection
        Tmax_cor    = Tmax - lapse_rate * elevationCorrection
        
        """
        Correction of air pressure for DEM based altitude correction:          
        barometric formula
        """
        
        highResDEM  = np.maximum(0,highResDEM)
        
        Pres_cor    = Pres *( (Tmean / ( Tmean + lapse_rate * (highResDEM - resLowResDEM))) ** (g * Mo / (R_air * lapse_rate)))

    #CALCULATE EXTRATERRESTRIAL RADIATION
    #get day of year
    tt  = currentdate.timetuple()
    JULDAY = tt.tm_yday
#    #Latitude radians
    LatRad= lat*np.pi/180.0
    test = np.tan(LatRad)
#    ### water euivalent extraterrestial radiation ###    
#    # declination (rad)
    declin = 0.4093*(np.sin(((2.0*pi*JULDAY)/365.0)-1.405))
#    # sunset hour angle
    arccosInput = (-(np.tan(LatRad))*(np.tan(declin)))
#    
    arccosInput = np.minimum(1,arccosInput)
    arccosInput = np.maximum(-1,arccosInput)
    sunangle = np.arccos(arccosInput)
#    # distance of earth to sun
    distsun = 1+0.033*(np.cos((2*pi*JULDAY)/365.0))
    # Ra = water equivalent extra terrestiral radiation in MJ day-1
    Ra = ((24 * 60 * 0.082) / 3.14) * distsun * (sunangle*(np.sin(LatRad))*(np.sin(declin))+(np.cos(LatRad))*(np.cos(declin))*(np.sin(sunangle)))
    
    #CALCULATE ACTUAL VAPOR PRESSURE
    # saturation vapour pressure [Pa]
    es = lambda T:610.8*np.exp((17.27*(Tmean_cor-273.15))/((Tmean_cor-273.15)+237.3))
    es_min  = es(Tmin_cor)
    es_max  = es(Tmax_cor)
    es_mean = (es_min+es_max)/2.

    # actual vapour pressure
    ea = lambda Pres_cor, Q, eps: -(Q*Pres_cor)/((eps-1)*Q-eps)
    ea_mean = ea(Pres_cor, Q, eps)
    ea_mean_kPa = ea_mean / 1000
    
    #clear sky solar radiation MJ d-1
    Rso = np.maximum(0.1,((0.75+(2*0.00005)) * Ra))
    
    Rsin_MJ = 0.086400 * Rsin # * 86400 / 1.000.000
    
    # !! VECTOR T TOO LONG
    Rlnet_MJ = - sigma * ((Tmax_cor**4+Tmin_cor**4)/2) * (0.34 - 0.14 * np.sqrt(np.maximum(0,(ea_mean_kPa)))) * (1.35*np.minimum(1,(Rsin_MJ / Rso))-0.35)
    
    Rlnet_Watt = Rlnet_MJ / 0.086400
    
    Rnet  = np.maximum(0,((1-alpha)*Rsin + Rlnet_Watt))
    
    # vapour pressure deficit
    vpd = np.maximum(es_mean - ea_mean, 0.)
    
    # density of air [kg m-3]
    rho = Pres_cor/(Tmean_cor*R)
    
    # Latent heat [J kg-1]
    Lheat = (2.501-(0.002361*(Tmean_cor-273.15)))*1e6

    # slope of vapour pressure [Pa K-1]
    deltop  = 4098. *(610.8*np.exp((17.27*(Tmean_cor-273.15))/((Tmean_cor-273.15)+237.3)))
    delbase = ((Tmean_cor-273.15)+237.3)**2
    delta   = deltop/delbase

    # psychrometric constant
    gamma   = cp*Pres_cor/(eps*Lheat)
    
    # aerodynamic resistance
    z = 10 # height of wind speed variable (10 meters above surface)
    Wsp_2 = Wsp*4.87/(np.log(67.8*z-5.42))
    ra = 208./Wsp_2
    
    PETtop  = np.maximum((delta*Rnet + rho*cp*vpd/ra),1)
    PETbase = np.maximum((delta + gamma*(1+rs/ra)),1)
    PET     = np.maximum(PETtop/PETbase, 0)
    PETmm   = np.maximum((PET/Lheat*TimeStepSecs),0)
    
    return PETmm
    
def PriestleyTaylor(lat, currentdate, relevantDataFields, Tmax, Tmin, elevationCorrection, highResDEM, resLowResDEM, downscaling):
    
    """
    relevantDataFields : ['Temperature','DownwellingLongWaveRadiation','SurfaceAtmosphericPressure',\
                    'NearSurfaceSpecificHumidity','SurfaceIncidentShortwaveRadiation','NearSurfaceWindSpeed']
    """
    
    Tmean   =  relevantDataFields[0]
    Rlin    =  relevantDataFields[1]
    Pres    =  relevantDataFields[2]
    Q       =  relevantDataFields[3]
    Rsin    =  relevantDataFields[4]             

    alpha        = 0.23         # albedo, 0.23 [-]
    sigma        = 4.903e-9     # stephan boltzmann [MJ K-4 m-2 day-1]
    cp_pt        = 0.001013     # specific heat of air 1013 [MJ kg-1 C-1]
    a            = 1.26         # Priestley-Taylor coefficient [-]
    eps          = 0.622        # ratio of molecular weight of water to dry air [-]
    g            = 9.81         # gravitational constant [m s-2]
    R_air        = 8.3144621    # specific gas constant for dry air [J mol-1 K-1]
    Mo           = 0.0289644    # molecular weight of gas [g / mol]
    lapse_rate   = 0.006        # lapse rate [K m-1]
    
    """ http://agsys.cra-cin.it/tools/evapotranspiration/help/Priestley-Taylor.html """
    if downscaling == 'True':
        lapse_rate = 0.006 # [ K m-1 ]
        #apply elevation correction
        Tmean_cor   = Tmean - lapse_rate * elevationCorrection
        Tmin_cor    = Tmin - lapse_rate * elevationCorrection
        Tmax_cor    = Tmax - lapse_rate * elevationCorrection
        """
        Correction of air pressure for DEM based altitude correction:          
        Barometric formula
        """

        highResDEM  = np.maximum(0,highResDEM)
        
        Pres_cor    = Pres *( (Tmean / ( Tmean + lapse_rate * (highResDEM - resLowResDEM))) ** (g * Mo / (R_air * lapse_rate)))
    
    #CALCULATE EXTRATERRESTRIAL RADIATION
    #get day of year
    tt  = currentdate.timetuple()
    JULDAY = tt.tm_yday
#    #Latitude radians
    LatRad= lat*np.pi/180.0
    test = np.tan(LatRad)
#    ### water euivalent extraterrestial radiation ###    
#    # declination (rad)
    declin = 0.4093*(np.sin(((2.0*pi*JULDAY)/365.0)-1.405))
#    # sunset hour angle
    arccosInput = (-(np.tan(LatRad))*(np.tan(declin)))
#    
    arccosInput = np.minimum(1,arccosInput)
    arccosInput = np.maximum(-1,arccosInput)
    sunangle = np.arccos(arccosInput)
#    # distance of earth to sun
    distsun = 1+0.033*(np.cos((2*pi*JULDAY)/365.0))
    # Ra = water equivalent extra terrestiral radiation in MJ day-1
    Ra = ((24 * 60 * 0.082) / 3.14) * distsun * (sunangle*(np.sin(LatRad))*(np.sin(declin))+(np.cos(LatRad))*(np.cos(declin))*(np.sin(sunangle)))
    
    #CALCULATE ACTUAL VAPOR PRESSURE
    # saturation vapour pressure [Pa]
    es = lambda T:610.8*np.exp((17.27*(Tmean_cor-273.15))/((Tmean_cor-273.15)+237.3))
    es_min  = es(Tmin_cor)
    es_max  = es(Tmax_cor)
    es_mean = (es_min+es_max)/2.

    # actual vapour pressure
    ea = lambda Pres_cor, Q, eps: -(Q*Pres_cor)/((eps-1)*Q-eps)
    ea_mean = ea(Pres_cor, Q, eps)
    ea_mean_kPa = ea_mean / 1000
        
    #clear sky solar radiation MJ d-1
    Rso = np.maximum(0.1,((0.75+(2*0.00005)) * Ra))
    
    Rsin_MJ = 0.086400 * Rsin # * 86400 / 1.000.000
    
    Rlnet_MJ = - sigma * ((Tmax_cor**4+Tmin_cor**4)/2) * (0.34 - 0.14 * np.sqrt(np.maximum(0,(ea_mean_kPa)))) * (1.35*np.minimum(1,(Rsin_MJ / Rso))-0.35)
    
    Rlnet_Watt = Rlnet_MJ / 0.086400
        
    preskPa     = Pres_cor / 1000 
    latentHeat  = 2.501 - ( 0.002361 * ( Tmean_cor - 273.15 ) ) # latent heat of vaporization (MJ kg-1)

    slope_exp   = (17.27*(Tmean_cor - 273.15)) / ((Tmean_cor - 273.15) + 237.3)
    slope_div   = ((Tmean_cor - 273.15) + 237.3)**2
    slope       = 4098 * (0.6108 * (np.exp(slope_exp))) / slope_div
    
    psychConst  = cp_pt * ( preskPa ) / (latentHeat * eps ) # psychrometric constant (kPa degreesC-1)
          
    # net radiation [MJ m-2]
    Rnet  = np.maximum(0,((1-alpha) *Rsin_MJ + Rlnet_MJ))
    
    PETmm = a * (1 / latentHeat) *( (slope * Rnet ) /  ( slope + psychConst ) )
    
    return PETmm
    
def hargreaves(lat, currentdate, relevantDataFields, Tmax, Tmin, elevationCorrection, downscaling):
    
    Tmean = relevantDataFields[0]

    if downscaling == 'True':
        lapse_rate = 0.006 # [ K m-1 ]
        #apply elevation correction
        Tmean_cor   = Tmean - lapse_rate * elevationCorrection
        Tmin_cor    = Tmin - lapse_rate * elevationCorrection
        Tmax_cor    = Tmax - lapse_rate * elevationCorrection
        
    #get day of year
    tt  = currentdate.timetuple()
    JULDAY = tt.tm_yday
#    #Latitude radians
    LatRad= lat*np.pi/180.0
    test = np.tan(LatRad)
#    ### water euivalent extraterrestial radiation ###    
#    # declination (rad)
    declin = 0.4093*(np.sin(((2.0*pi*JULDAY)/365.0)-1.405))
#    # sunset hour angle
    arccosInput = (-(np.tan(LatRad))*(np.tan(declin)))
#    
    arccosInput = np.minimum(1,arccosInput)
    arccosInput = np.maximum(-1,arccosInput)
    sunangle = np.arccos(arccosInput)
#    # distance of earth to sun
    distsun = 1+0.033*(np.cos((2*pi*JULDAY)/365.0))
#    # SO = water equivalent extra terrestiral radiation in mm/day
    Ra = 15.392*distsun*(sunangle*(np.sin(LatRad))*(np.sin(declin))+(np.cos(LatRad))*(np.cos(declin))*(np.sin(sunangle)))
    strDay       = str(JULDAY)

    airT = relevantDataFields[0]
    PETmm = 0.0023*Ra*((np.maximum(0,(Tmean_cor-273.0))) + 17.8)*sqrt(np.maximum(0,(Tmax_cor-Tmin_cor)))
 
    return PETmm, Ra, LatRad, sunangle, declin

#### MAIN ####

def main(argv=None):

    # Set all sorts of defaults.....
    serverroot = "http://wci.earth2observe.eu/thredds/dodsC/"
    wrrsetroot = "ecmwf/met_forcing_v0/"
    
    #available variables with corresponding file names and standard_names as in NC files
    variables = ['Temperature','DownwellingLongWaveRadiation','SurfaceAtmosphericPressure',\
                    'NearSurfaceSpecificHumidity','Rainfall','SurfaceIncidentShortwaveRadiation','SnowfallRate','NearSurfaceWindSpeed']
    filenames = ["Tair_daily_E2OBS_","LWdown_daily_E2OBS_","PSurf_daily_E2OBS_","Qair_daily_E2OBS_",\
                    "Rainf_daily_E2OBS_","SWdown_daily_E2OBS_","Snowf_daily_E2OBS_","Wind_daily_E2OBS_"]
    standard_names = ['air_temperature','surface_downwelling_longwave_flux_in_air','surface_air_pressure','specific_humidity',\
                        'rainfal_flux','surface_downwelling_shortwave_flux_in_air','snowfall_flux','wind_speed']
    prefixes = ["Tmean","LWdown","PSurf","Qair",\
                    "Rainf","SWdown","Snowf","Wind"]
    
    #tempdir

    #defaults in absence of ini file
    filename = "Tair_daily_E2OBS_"
    standard_name ='air_temperature'
    startyear = 1980
    endyear= 1980
    startmonth = 1
    endmonth = 12
    latmin = 51.25
    latmax = 51.75
    lonmin = 5.25
    lonmax = 5.75
    startday = 1
    endday = 1
    getDataForVar = True
    calculateEvap = False
    evapMethod = None
    downscaling = None
    nrcalls = 0
    loglevel=logging.INFO

    if argv is None:
        argv = sys.argv[1:]
        if len(argv) == 0:
            usage()
            exit()   
    try:
        opts, args = getopt.getopt(argv, 'I:')
    except getopt.error, msg:
        usage(msg)
    
    for o, a in opts:
        if o == '-I': inifile = a
    
    logger = setlogger("e2o_calculateEvap.log","e2o_calculateEvaporation",level=loglevel)
    #logger, ch = setlogger("e2o_getvar.log","e2o_getvar",level=loglevel)
    logger.info("Reading settings from ini: " + inifile)
    theconf = iniFileSetUp(a)
    
    # Read period from file
    startyear = int(configget(logger,theconf,"selection","startyear",str(startyear)))
    endyear = int(configget(logger,theconf,"selection","endyear",str(endyear)))
    endmonth = int(configget(logger,theconf,"selection","endmonth",str(endmonth)))
    startmonth = int(configget(logger,theconf,"selection","startmonth",str(startmonth)))
    endday = int(configget(logger,theconf,"selection","endday",str(endday)))
    startday = int(configget(logger,theconf,"selection","startday",str(startday)))
    start = datetime.datetime(startyear,startmonth,startday)
    end = datetime.datetime(endyear,endmonth,endday)
        
    #read remaining settings from in file        
    lonmax = float(configget(logger,theconf,"selection","lonmax",str(lonmax)))
    lonmin = float(configget(logger,theconf,"selection","lonmin",str(lonmin)))
    latmax = float(configget(logger,theconf,"selection","latmax",str(latmax)))
    latmin = float(configget(logger,theconf,"selection","latmin",str(latmin)))
    BB = dict(
           lon=[ lonmin, lonmax],
           lat= [ latmin, latmax]
           )
    serverroot = configget(logger,theconf,"url","serverroot",serverroot)
    wrrsetroot = configget(logger,theconf,"url","wrrsetroot",wrrsetroot)
    oformat = configget(logger,theconf,"output","format","PCRaster")
    odir = configget(logger,theconf,"output","directory","output/")
    oprefix = configget(logger,theconf,"output","prefix","E2O")

    # Check whether downscaling should be applied
    downscaling   = configget(logger,theconf,"selection","downscaling",downscaling)
    if downscaling == 'True':
        #get grid info
        resX, resY, cols, rows, highResLon, highResLat, data, FillVal = readMap((os.path.join('highResDEM','DEM.tif')),'GTiff',logger)
       
        elevationCorrection, highResDEM, resLowResDEM = resampleDEM('highResDEM','lowResDEM',logger)

    #Check whether evaporation should be calculated
    calculateEvap   = configget(logger,theconf,"selection","calculateEvap",calculateEvap)

    if calculateEvap == 'True':
        evapMethod      = configget(logger,theconf,"selection","evapMethod",evapMethod)
                
    if evapMethod == 'PenmanMonteith':
        relevantVars = ['Temperature','DownwellingLongWaveRadiation','SurfaceAtmosphericPressure',\
                    'NearSurfaceSpecificHumidity','SurfaceIncidentShortwaveRadiation','NearSurfaceWindSpeed']        
    elif evapMethod == 'Hargreaves':
        relevantVars = ['Temperature']
    elif evapMethod == 'PriestleyTaylor':
        relevantVars = ['Temperature','DownwellingLongWaveRadiation','SurfaceAtmosphericPressure',\
                    'NearSurfaceSpecificHumidity','SurfaceIncidentShortwaveRadiation']
        
    currentdate = start
    ncnt = 0
    while currentdate <= end:
        ncnt += 1
        if ncnt > 0:
            # Get all daily datafields needed and aad to list
            relevantDataFields = []
            for i in range (0,len(variables)):
                if variables[i] in relevantVars:
                    mapname = os.path.join(odir,getmapname(ncnt,oprefix))
                    if os.path.exists(mapname):
                        logger.info("Skipping map: " + mapname)
                    else:
                        logger.info("Getting data field: " + filename)
                        filename = filenames[i]
                        standard_name = standard_names[i]
                        logger.info("Get file list..")
                        tlist, timelist = get_times_daily(currentdate,currentdate,serverroot, wrrsetroot, filename,logger)
                        logger.info("Get dates..")
    
                        ncstepobj = getstepdaily(tlist,BB,standard_name,logger)
    
                        logger.info("Get data...: " + str(timelist))
                        mstack = ncstepobj.getdates(timelist)
                        mean_as_map = mstack.mean(axis=0)
                        logger.info("Get data body...")
                        if downscaling == 'True':
                            save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,mean_as_map,int(ncnt),'temp',prefixes[i],oformat='GTiff')                     
                            mean_as_map = resample('highResDEM',prefixes[i],int(ncnt),logger)
                            if variables[i] == 'SurfaceIncidentShortwaveRadiation':
                                mean_as_map     = correctRsin(mean_as_map,currentdate,'radiationCor',logger)
                                
                                
                        relevantDataFields.append(mean_as_map)
                                            
    
            if evapMethod == 'PenmanMonteith':
    
                mapname = os.path.join(odir,getmapname(ncnt,oprefix))
                if os.path.exists(mapname):
                    logger.info("Skipping map: " + mapname)
                else:
                    # retrieve 3 hourly Temperature and calculate max and min Temperature
                    filename = 'Tair_E2OBS_'
                    standard_name = 'air_temperature'
                    timestepSeconds = 10800
                
                    tlist, timelist = get_times(currentdate,currentdate,serverroot, wrrsetroot, filename,timestepSeconds,logger )
                    ncstepobj = getstep(tlist,BB,standard_name,timestepSeconds,logger)
                    mstack = ncstepobj.getdates(timelist)
                    tmin = mstack.min(axis=0)
                    tmax = mstack.max(axis=0)
                    if downscaling == 'True':
                        save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,tmin,int(ncnt),'temp','tmin',oformat=oformat)                     
                        save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,tmax,int(ncnt),'temp','tmax',oformat=oformat)                     
                        tmin = resample('highResDEM','tmin',int(ncnt),logger)
                        tmax = resample('highResDEM','tmax',int(ncnt),logger)    
                        
                    #only needed once
                    if nrcalls ==0:
                        nrcalls = nrcalls + 1
                        latitude = ncstepobj.lat[:]
                        #assuming a resolution of 0.5 degrees
                        LATITUDE = np.ones(((2*(latmax-latmin)),(2*(lonmax-lonmin))))
                        for i in range (0,int((2*(lonmax-lonmin)))):
                            LATITUDE[:,i]=LATITUDE[:,i]*latitude
                        save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,LATITUDE,int(ncnt),'temp','lat',oformat=oformat)
                        LATITUDE = resample('highResDEM','lat',int(ncnt),logger)
                   
                                                       
                    PETmm = PenmanMonteith(LATITUDE, currentdate, relevantDataFields, tmax, tmin, elevationCorrection, highResDEM, resLowResDEM, downscaling)
                    logger.info("Saving PM PET data for: " +str(currentdate))
                    save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,np.flipud(PETmm),int(ncnt),odir,prefix=oprefix,oformat=oformat)
                  
            if evapMethod == 'PriestleyTaylor':
                mapname = os.path.join(odir,getmapname(ncnt,oprefix))
                if os.path.exists(mapname):
                    logger.info("Skipping map: " + mapname)
                else:
                    # retrieve 3 hourly Temperature and calculate min Temperature
                    filename = 'Tair_E2OBS_'
                    standard_name = 'air_temperature'
                    timestepSeconds = 10800
                
                    tlist, timelist = get_times(currentdate,currentdate,serverroot, wrrsetroot, filename,timestepSeconds,logger )
                    ncstepobj = getstep(tlist,BB,standard_name,timestepSeconds,logger)
                    mstack = ncstepobj.getdates(timelist)
                    tmin = mstack.min(axis=0)
                    tmax = mstack.max(axis=0)
                    if downscaling == 'True':
                        save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,tmin,int(ncnt),'temp','tmin',oformat=oformat)
                        save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,tmax,int(ncnt),'temp','tmax',oformat=oformat)
                        tmin = resample('highResDEM','tmin',int(ncnt),logger)
                        tmax = resample('highResDEM','tmax',int(ncnt),logger)   

                    #only needed once
                    if nrcalls ==0:
                        nrcalls = nrcalls + 1
                        latitude = ncstepobj.lat[:]
                        #assuming a resolution of 0.5 degrees
                        LATITUDE = np.ones(((2*(latmax-latmin)),(2*(lonmax-lonmin))))
                        for i in range (0,int((2*(lonmax-lonmin)))):
                            LATITUDE[:,i]=LATITUDE[:,i]*latitude
                        save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,LATITUDE,int(ncnt),'temp','lat',oformat=oformat)
                        LATITUDE = resample('highResDEM','lat',int(ncnt),logger)
                   
                    PETmm = PriestleyTaylor(LATITUDE, currentdate, relevantDataFields, tmax, tmin,elevationCorrection,highResDEM,resLowResDEM,downscaling)
                                   
                    logger.info("Saving PT PET data for: " +str(currentdate))

                    save_as_mapsstack_per_day(highResLat,highResLon,np.flipud(PETmm),int(ncnt),odir,prefix=oprefix,oformat=oformat)
                                  
            if evapMethod == 'Hargreaves':
                mapname = os.path.join(odir,getmapname(ncnt,oprefix))
                if os.path.exists(mapname):
                    logger.info("Skipping map: " + mapname)
                else:
                    # retrieve 3 hourly Temperature and calculate max and min Temperature
                    filename = 'Tair_E2OBS_'
                    standard_name = 'air_temperature'
                    timestepSeconds = 10800
    
                    logger.info("Get times 3 hr data..")
                    tlist, timelist = get_times(currentdate,currentdate,serverroot, wrrsetroot, filename,timestepSeconds,logger )
                    logger.info("Get actual 3hr data...")
                    ncstepobj = getstep(tlist,BB,standard_name,timestepSeconds,logger)
                    #ncstepobj = getstepdaily(tlist,BB,standard_name,logger)
    
                    mstack = ncstepobj.getdates(timelist)
    
                    #only needed once
                    if nrcalls ==0:
                        nrcalls = nrcalls + 1
                        latitude = ncstepobj.lat[:]
                        #assuming a resolution of 0.5 degrees
                        LATITUDE = np.ones(((2*(latmax-latmin)),(2*(lonmax-lonmin))))
                        for i in range (0,int((2*(lonmax-lonmin)))):
                            LATITUDE[:,i]=LATITUDE[:,i]*latitude
                        save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,LATITUDE,int(ncnt),'temp','lat',oformat=oformat)
                        LATITUDE = resample('highResDEM','lat',int(ncnt),logger)

                    tmin = mstack.min(axis=0)
                    tmax = mstack.max(axis=0)
                    if downscaling == 'True':
                        save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,tmin,int(ncnt),'temp','tmin',oformat=oformat)
                        save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,tmax,int(ncnt),'temp','tmax',oformat=oformat)
                        tmin = resample('highResDEM','tmin',int(ncnt),logger)
                        tmax = resample('highResDEM','tmax',int(ncnt),logger)  
                        
                    logger.info("Start hargreaves..")
                    PETmm, Ra, dst, angle, dec = hargreaves(LATITUDE,currentdate,relevantDataFields, tmax, tmin, elevationCorrection, downscaling)
                    #save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,Ra,int(ncnt),odir,prefix="RA",oformat=oformat)
    
                    logger.info("Saving HAR PET data for: " +str(currentdate))
                    #save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,PETmm[0],int(ncnt),odir,prefix=oprefix,oformat=oformat)
                    save_as_mapsstack_per_day(ncstepobj.lat,ncstepobj.lon,np.flipud(PETmm),int(ncnt),odir,prefix=oprefix,oformat=oformat)
            
#            #cleaning temp and resample directory
#            dirs = ['temp','resampled']
#            for directory in dirs:
#                fileList = os.listdir(directory)
#                for fileName in fileList:
#                    os.remove(directory+"/"+fileName)
        
        else:
            pass
        
        currentdate += datetime.timedelta(days=1)       
    
    logger.info("Done.")

if __name__ == "__main__":
    main()



