
#!/usr/bin/env python

import os, logging
try:
    from os import scandir
except ImportError:
    from scandir import scandir  # use scandir PyPI module on Python < 3.5

class StorageManager():
    """ Maintain folder where files are stored
    This class checks if folder exist, if not, then create it
    Removes most oldest files incase the size is exceeds limits
    """
    def __init__(self, storage_path, storage_max_size):
        self.storage_path = storage_path
        self.storage_max_size = storage_max_size
        self.force_create_path(storage_path)

    def force_create_file_path(self, filename):
        path = os.path.dirname(filename)
        self.force_create_path(path)

    def force_create_path(self, path):
        if not os.path.exists(path):
            logging.debug(f'path not existing: {path} \n try to create it..')
            try:
                os.makedirs(path)
            except:
                logging.exception(f'Can''t create path: {path}')

    def cleanup(self):
        """function removes old files in Camera folder. This gives ability to write files in neverending loop, when old records are rewritedby new ones"""
        try:            
            max_size = self.storage_max_size*1024*1024*1024
            logging.debug(f"[{self.name}] Start storage cleanup on path: {self.storage_path} (Max size: {max_size/1024/1024/1024:.2f} GB)")
            self.file_list = []
            self.folder_size(self.storage_path)
            # sort list of files by datetime value (DESC)
            self.file_list = sorted(self.file_list, key=itemgetter('dt'), reverse=True)
            # calculate cumulative size
            i = 0
            cumsum = 0
            for item in self.file_list:
                cumsum += item['size']
                item['cumsum'] = cumsum
                if(cumsum > max_size):
                    i = i + 1               
                    logging.info(f"[{self.name}] Removing file {i}: {item['file']}")
                    os.remove(item['file'])
                    self.mqtt_client.publish(self.cnfg['mqtt']['topic_publish'].format(source_name=self.name)
                        , json.dumps({
                                        'status': self.state_msg,
                                        'deleted': item['file']
                                        })
                    )
            # remove empty directories
            for (_path, _dirs, _files) in os.walk(self.storage_path, topdown=False):
                if _files or _dirs: continue # skip remove
                try:
                    os.rmdir(_path)
                    logging.debug(f'Remove empty folder: {_path}')
                except OSError:
                    logging.exception(' Folder not empty :')
        except:
            logging.exception(f"Storage Cleanup Error")

    def folder_size(self, path='.'):
        total = 0
        for entry in scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
                row = {
                            'file': entry.path,
                            'size': entry.stat().st_size,
                            'dt': entry.stat().st_mtime, # have to use last modification time because of Linux: there is no easy way to get correct creation time value
                         }
                self.file_list.append(row)
            elif entry.is_dir(follow_symlinks=False):
                total += self.folder_size(entry.path)
        return total