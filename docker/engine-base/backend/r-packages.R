options(
  Ncpus = min(4L, max(1L, parallel::detectCores(logical = TRUE) - 1L)),
  BIOCONDUCTOR_USE_CONTAINER_REPOSITORY = TRUE,
  repos = c(CRAN = "https://packagemanager.posit.co/cran/__linux__/noble/latest")
)

script_args <- commandArgs(trailingOnly = FALSE)
script_file_arg <- grep("^--file=", script_args, value = TRUE)
script_dir <- if (length(script_file_arg) > 0) {
  dirname(normalizePath(sub("^--file=", "", script_file_arg[[1]])))
} else {
  getwd()
}

source(file.path(script_dir, "r-package-list.R"), local = TRUE)

install_stage <- Sys.getenv("R_PACKAGE_STAGE", unset = "all")
valid_stages <- c("cran", "bioc", "github", "all")
if (!install_stage %in% valid_stages) {
  stop("Unknown R_PACKAGE_STAGE: ", install_stage)
}

package_present <- function(pkg) {
  isTRUE(requireNamespace(pkg, quietly = TRUE))
}

runtime_dependencies <- NA
cran_repo <- "https://packagemanager.posit.co/cran/__linux__/noble/latest"

missing_packages <- function(packages) {
  packages[!vapply(packages, package_present, logical(1))]
}

install_cran_packages <- function(packages) {
  packages <- missing_packages(packages)
  if (length(packages) == 0) {
    cat("CRAN stage already satisfied.\n")
    return(invisible(TRUE))
  }

  cat("Installing CRAN package set:", paste(packages, collapse = ", "), "\n")
  tryCatch(
    {
      install.packages(
        packages,
        repos = cran_repo,
        dependencies = runtime_dependencies,
        Ncpus = getOption("Ncpus", 1L)
      )
      TRUE
    },
    error = function(error) {
      cat("CRAN batch install error:", conditionMessage(error), "\n")
      FALSE
    }
  )

  # Fallback to individual package install if batch missed anything
  still_missing <- missing_packages(packages)
  if (length(still_missing) > 0) {
    cat("Retrying missing CRAN packages individually:", paste(still_missing, collapse = ", "), "\n")
    for (pkg in still_missing) {
      install_cran_package(pkg)
    }
  }
}

install_bioc_packages <- function(packages) {
  packages <- missing_packages(packages)
  if (length(packages) == 0) {
    cat("Bioconductor stage already satisfied.\n")
    return(invisible(TRUE))
  }

  cat("Installing Bioconductor package set:", paste(packages, collapse = ", "), "\n")
  tryCatch(
    {
      BiocManager::install(
        packages,
        ask = FALSE,
        update = FALSE,
        dependencies = runtime_dependencies,
        Ncpus = 1L,
        build_vignettes = FALSE
      )
      TRUE
    },
    error = function(error) {
      cat("Bioconductor batch install error:", conditionMessage(error), "\n")
      FALSE
    }
  )
}

install_cran_package <- function(pkg) {
  if (package_present(pkg)) {
    cat("CRAN already installed:", pkg, "\n")
    return(TRUE)
  }

  cat("Installing CRAN package:", pkg, "\n")
  status <- tryCatch(
    {
      install.packages(
        pkg,
        repos = cran_repo,
        dependencies = runtime_dependencies,
        Ncpus = 2L
      )
      package_present(pkg)
    },
    error = function(error) {
      cat("CRAN install error for", pkg, ":", conditionMessage(error), "\n")
      FALSE
    }
  )

  if (!status) {
    cat("CRAN install failed for:", pkg, "\n")
  }
  status
}

install_bioc_package <- function(pkg) {
  if (package_present(pkg)) {
    cat("Bioconductor already installed:", pkg, "\n")
    return(TRUE)
  }

  cat("Installing Bioconductor package:", pkg, "\n")
  status <- tryCatch(
    {
      BiocManager::install(
        pkg,
        ask = FALSE,
        update = FALSE,
        dependencies = runtime_dependencies,
        Ncpus = 1L,
        build_vignettes = FALSE
      )
      package_present(pkg)
    },
    error = function(error) {
      cat("Bioconductor install error for", pkg, ":", conditionMessage(error), "\n")
      FALSE
    }
  )

  if (!status) {
    cat("Bioconductor install failed for:", pkg, "\n")
  }
  status
}

install_github_package <- function(repo) {
  pkg <- basename(repo)
  if (package_present(pkg)) {
    cat("GitHub package already installed:", pkg, "\n")
    return(TRUE)
  }

  if (!package_present("remotes")) {
    install_cran_package("remotes")
  }

  cat("Installing GitHub package:", repo, "\n")
  status <- tryCatch(
    {
      remotes::install_github(
        repo,
        upgrade = "never",
        dependencies = runtime_dependencies,
        quiet = FALSE,
        force = FALSE,
        build_vignettes = FALSE
      )
      package_present(pkg)
    },
    error = function(error) {
      cat("GitHub install error for", repo, ":", conditionMessage(error), "\n")
      FALSE
    }
  )

  if (!status) {
    cat("GitHub install failed for:", repo, "\n")
  }
  status
}

if (!package_present("BiocManager")) {
  install.packages("BiocManager", repos = "https://cloud.r-project.org")
}

options(repos = BiocManager::repositories(type = "both"))
cat("Bioconductor version:", as.character(BiocManager::version()), "\n")

if (install_stage %in% c("cran", "all")) {
  install_cran_packages(cran_packages)
}

if (install_stage %in% c("bioc", "all")) {
  install_bioc_packages(bioc_packages)
}

if (install_stage %in% c("github", "all")) {
  for (repo in github_packages) {
    install_github_package(repo)
  }
}

all_required <- unique(c(cran_packages, bioc_packages, vapply(github_packages, basename, character(1))))
required_for_stage <- unique(switch(
  install_stage,
  cran = cran_packages,
  bioc = bioc_packages,
  github = vapply(github_packages, basename, character(1)),
  all = all_required
))
missing_final <- missing_packages(required_for_stage)

if (install_stage == "all" && length(missing_final) > 0) {
  cat("Retrying missing packages individually:", paste(missing_final, collapse = ", "), "\n")
  for (pkg in missing_final) {
    if (pkg %in% cran_packages) {
      install_cran_package(pkg)
    } else if (pkg %in% bioc_packages) {
      install_bioc_package(pkg)
    } else {
      repo <- github_packages[vapply(github_packages, basename, character(1)) == pkg]
      if (length(repo) > 0) {
        install_github_package(repo[[1]])
      }
    }
  }

  missing_final <- missing_packages(all_required)
}

if (length(missing_final) > 0) {
  stop("R package install incomplete for stage '", install_stage, "'. Missing: ", paste(missing_final, collapse = ", "))
}

cat("R package stage complete:", install_stage, "\n")
