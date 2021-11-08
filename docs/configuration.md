Test configuration file should be created in JSON format.
Here you can see all available options with descriptions
> Note: comments (text after # sign) are not allowed in JSON format and provided only in this manual to describe options
> 
> Note2: `OPTIONAL` means that key could be omitted
```json
{
    "project_name1": { # <-- project name, value is an dictionary
        "distribution": { # <-- distribution configuration
            "cores": 2, # <-- number of cores required to run this project
            "distribution_types": [ # <-- (OPTIONAL) (default: None) list of distribution types required for the project
                "Variations",
                "Frequencies"
            ],
            "parametric_tasks": 1, # <-- (OPTIONAL) (default: 2) if project has parametric sweep, number of design 
                                    # points to run in parallel
            "multilevel_distribution_tasks": 0,  # (OPTIONAL) (default: 0) <-- if distribution_types is activated 
                                                  # and multilevel is required,
                                                  # then set number of tasks on the 1st level
            "single_node": true # <-- (OPTIONAL) (default: false) forces project to be solved within single node
        },
        "path": "input\\just_winding.aedt", # <-- (OPTIONAL) (default: project name + .aedt in current working 
                                          # directory, eg <cwd>/project_name1.aedt) Supports full and relative paths
                                          # if relative path is specified, folder structure would be preserved 
                                          # during run
        "dependencies": ["input\\nested\\ctrl_prog"],  # <-- (OPTIONAL) (default: None) specifies file(s)/folder(s) to 
                                                      # carry on with project, eg control program script. Format: 
                                                      # (string) path or (list[str]) paths. Path could be relative or
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