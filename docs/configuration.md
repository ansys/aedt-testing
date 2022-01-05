The test configuration file should be created in JSON format.
Here is an example showing all available options  along with descriptions.
> Note 1: comments (text after # sign) are not allowed in the JSON format and provided 
> below for information only.
> 
> Note 2: `OPTIONAL` is used to denote a key that may be omitted.
```json
{
    "project_name1": { # <-- project name, value is an dictionary
        "distribution": { # <-- distribution configuration
            "cores": 2, # <-- number of cores used when this project is run
            "distribution_types": [ # <-- (OPTIONAL) (default: None) list of parallel distribution types.
                "Variations",
                "Frequencies"
            ],
            "parametric_tasks": 1, # <-- (OPTIONAL) (default: 2) if project runs a parametric sweep, this is the number
                                    # of tasks to run in parallel
            "multilevel_distribution_tasks": 0,  # (OPTIONAL) (default: 0) <-- if distribution_types is defined 
                                                  # and multilevel is required,
                                                  # then set the number of tasks for the 1st level
            "single_node": true # <-- (OPTIONAL) (default: false) forces project to be solved on a single node
        },
        "path": "input\\just_winding.aedt", # <-- (OPTIONAL) (default: project name + .aedt in current working 
                                          # directory, eg <cwd>/project_name1.aedt) Supports full and relative paths.
                                          # If the path is relative, the folder structure will be preserved 
                                          # during execution.
        "dependencies": ["input\\nested\\ctrl_prog"],  # <-- (OPTIONAL) (default: None) specifies file(s)/folder(s)  
                                                      # dependencies for the project, e.g. the "control program" script. 
                                                      # in Maxwelll. The format is: 
                                                      # (string) path or (list[str]) paths. Path may be relative or
                                                      # absolute
    },
    "project_name2": {
        "distribution": {
            "cores": 1,
            "distribution_types": [
                "default"
            ]
        },
    },
    "project_name3": {
    <--- --->
    }
}
```